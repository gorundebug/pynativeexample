from __future__ import annotations

import asyncio
import os
from typing import Any

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


class BenchmarkGrpcServerInterceptor(grpc.aio.ServerInterceptor):
    async def intercept_service(
        self, continuation: Any, handler_call_details: Any
    ) -> Any:
        return await continuation(handler_call_details)


class InventoryService(inventoryserviceapi_pb2_grpc.InventoryServiceApiServicer):
    def __init__(self, delay: float) -> None:
        self._delay = delay
        self._stock = {"SKU-001": 100, "SKU-002": 50, "SKU-003": 25}

    async def ProcessOrderItem(
        self, request: Any, context: grpc.aio.ServicerContext
    ) -> Any:
        del context
        if self._delay:
            await asyncio.sleep(self._delay)
        # No await occurs between read and update. On this service's single
        # asyncio event loop the reservation is therefore one uninterrupted
        # operation and needs no coroutine-level lock.
        available = self._stock.get(request.sku, 0)
        reserved = available >= request.quantity
        if reserved:
            self._stock[request.sku] = available - request.quantity
        return processorderitem_pb2.ProcessOrderItemResponse(
            available_qty=request.quantity if reserved else available,
            reserved=reserved,
            status="CONFIRMED" if reserved else "OUT_OF_STOCK",
        )


async def serve() -> None:
    delay = env_duration("INVENTORY_SERVICE_RESPONSE_DELAY", 0.0)
    grpc_server = grpc.aio.server(interceptors=[BenchmarkGrpcServerInterceptor()])
    inventoryserviceapi_pb2_grpc.add_InventoryServiceApiServicer_to_server(
        InventoryService(delay), grpc_server
    )
    grpc_server.add_insecure_port(
        f"{os.getenv('INVENTORY_SERVICE_GRPC_HOST', '0.0.0.0')}:"
        f"{os.getenv('INVENTORY_SERVICE_GRPC_PORT', '9202')}"
    )
    await grpc_server.start()

    app = web.Application(middlewares=[benchmark_http_middleware])
    app.add_routes(status_routes("inventoryservice"))
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(
        runner,
        os.getenv("INVENTORY_SERVICE_HTTP_HOST", "0.0.0.0"),
        int(os.getenv("INVENTORY_SERVICE_HTTP_PORT", "9092")),
    )
    await site.start()
    await wait_for_signal()
    await runner.cleanup()
    await grpc_server.stop(5)


if __name__ == "__main__":
    asyncio.run(serve())
