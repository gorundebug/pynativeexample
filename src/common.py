from __future__ import annotations

import asyncio
import os
import signal
from datetime import datetime, timezone
from typing import Any

from aiohttp import web


@web.middleware
async def benchmark_http_middleware(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    return await handler(request)


def env_duration(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    units = {"ms": 0.001, "s": 1.0, "m": 60.0}
    for suffix, multiplier in units.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * multiplier
    return float(value)


def status_routes(service: str) -> list[web.RouteDef]:
    started_at = datetime.now(timezone.utc).isoformat()

    async def status(_: web.Request) -> web.Response:
        return web.json_response(
            {"service": service, "status": "ok", "started_at": started_at}
        )

    async def metrics(_: web.Request) -> web.Response:
        return web.Response(
            text="# No ServiceLib runtime metrics in the native baseline.\n"
        )

    return [web.get("/status/data", status), web.get("/metrics", metrics)]


async def wait_for_signal() -> None:
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, event.set)
    await event.wait()
