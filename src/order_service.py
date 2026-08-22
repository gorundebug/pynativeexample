from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import grpc
from aiohttp import web

import inventoryserviceapi_pb2_grpc
import processorderitem_pb2
from common import (
    benchmark_http_middleware,
    env_duration,
    status_routes,
    wait_for_signal,
)


class BenchmarkGrpcClientInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    async def intercept_unary_unary(
        self,
        continuation: Any,
        client_call_details: Any,
        request: Any,
    ) -> Any:
        return await continuation(client_call_details, request)


class OrderService:
    def __init__(
        self, address: str, connections: int, timeout: float, soft_margin: float
    ) -> None:
        if connections <= 0:
            raise ValueError(
                "INVENTORY_SERVICE_API_CONNECTIONS_COUNT must be positive"
            )
        self._timeout = timeout
        self._soft_timeout = timeout - soft_margin
        if self._soft_timeout < 0:
            raise ValueError("soft deadline margin must not exceed request timeout")
        self._channels = [
            grpc.aio.insecure_channel(
                address,
                interceptors=[BenchmarkGrpcClientInterceptor()],
            )
            for _ in range(connections)
        ]
        self._stubs = [
            inventoryserviceapi_pb2_grpc.InventoryServiceApiStub(channel)
            for channel in self._channels
        ]
        self._next = 0

    async def close(self) -> None:
        await asyncio.gather(*(channel.close() for channel in self._channels))

    def _stub(self) -> Any:
        stub = self._stubs[self._next % len(self._stubs)]
        self._next += 1
        return stub

    async def process_order(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception as error:
            raise web.HTTPBadRequest(text="invalid JSON body\n") from error
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="JSON body must be an object\n")
        raw_items = body.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise web.HTTPBadRequest(text="items must not be empty\n")

        order_id = request.headers.get("X-Request-ID") or str(uuid4())
        items: list[dict[str, Any]] = []
        original_total = 0.0
        for raw in raw_items:
            if not isinstance(raw, dict):
                raise web.HTTPBadRequest(text="each item must be an object\n")
            quantity = int(raw.get("quantity", 0))
            if quantity <= 0:
                raise web.HTTPBadRequest(text="all quantities must be positive\n")
            price = float(raw.get("unit_price", raw.get("unitPrice", 0.0)))
            item = {
                "item_id": str(raw.get("item_id", raw.get("itemId", ""))),
                "sku": str(raw.get("sku", "")),
                "quantity": quantity,
                "unit_price": price,
            }
            items.append(item)
            original_total += quantity * price

        results: list[dict[str, Any]] = []
        try:
            async with asyncio.timeout(self._soft_timeout):
                for item in items:
                    results.append(await self._process_item(order_id, item))
        except TimeoutError:
            return self._response(order_id, "TIMED_OUT", original_total, [])

        status = (
            "CONFIRMED"
            if all(item["reserved"] for item in results)
            else "PARTIALLY_CONFIRMED"
        )
        total = sum(
            item["unit_price"] * item["requested_qty"] for item in results
        )
        return self._response(order_id, status, total, results)

    async def _process_item(
        self, order_id: str, item: dict[str, Any]
    ) -> dict[str, Any]:
        result = {
            "item_id": item["item_id"],
            "sku": item["sku"],
            "requested_qty": item["quantity"],
            "available_qty": 0,
            "reserved": False,
            "status": "PROCESSING_ERROR",
            "unit_price": item["unit_price"],
        }
        try:
            response = await self._stub().ProcessOrderItem(
                processorderitem_pb2.ProcessOrderItemRequest(
                    order_id=order_id,
                    item_id=item["item_id"],
                    sku=item["sku"],
                    quantity=item["quantity"],
                ),
                timeout=self._timeout,
            )
        except grpc.aio.AioRpcError as error:
            result["error"] = str(error)
            return result
        result.update(
            available_qty=response.available_qty,
            reserved=response.reserved,
            status=response.status,
        )
        return result

    @staticmethod
    def _response(
        order_id: str,
        status: str,
        total: float,
        results: list[dict[str, Any]],
    ) -> web.Response:
        confirmed = []
        for result in results:
            item = {
                "item_id": result["item_id"],
                "sku": result["sku"],
                "available_qty": result["available_qty"],
                "reserved": result["reserved"],
                "status": result["status"],
            }
            if error := result.get("error"):
                item["error"] = error
            confirmed.append(item)
        payload: dict[str, Any] = {
            "order_id": order_id,
            "status": status,
            "total_amount": total,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
        if confirmed:
            payload["confirmed_items"] = confirmed
        return web.json_response(payload)


async def serve() -> None:
    service = OrderService(
        os.getenv("INVENTORY_SERVICE_API_ADDRESS", "inventoryservice:9202"),
        int(os.getenv("INVENTORY_SERVICE_API_CONNECTIONS_COUNT", "1")),
        env_duration("ORDER_SERVICE_REQUEST_TIMEOUT", 5.0),
        env_duration("ORDER_SERVICE_SOFT_DEADLINE_MARGIN", 1.0),
    )
    app = web.Application(middlewares=[benchmark_http_middleware])
    app.add_routes(
        [
            web.post("/v1/processorder", service.process_order),
            *status_routes("orderservice"),
        ]
    )
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(
        runner,
        os.getenv("ORDER_SERVICE_HTTP_HOST", "0.0.0.0"),
        int(os.getenv("ORDER_SERVICE_HTTP_PORT", "9091")),
    )
    await site.start()
    await wait_for_signal()
    await runner.cleanup()
    await service.close()


if __name__ == "__main__":
    asyncio.run(serve())
