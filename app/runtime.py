from __future__ import annotations

import asyncio
import logging

import uvicorn

from .config import settings
from .db import init_db
from .main import run_bot

log = logging.getLogger("production_account_runtime")


async def _run_miniapp() -> None:
    config = uvicorn.Config(
        "webapp.server:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=settings.proxy_headers,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        access_log=True,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    settings.require_ready()
    init_db()
    tasks: list[asyncio.Task] = []
    if settings.miniapp_enabled:
        tasks.append(asyncio.create_task(_run_miniapp(), name="miniapp"))
    if settings.bot_enabled:
        tasks.append(asyncio.create_task(run_bot(), name="bot"))
    if not tasks:
        raise RuntimeError("Нет включённых сервисов.")
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        error: BaseException | None = None
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except BaseException as exc:
                error = exc
                log.exception("Один из процессов остановился с ошибкой", exc_info=exc)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if error:
            raise error
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    asyncio.run(main())
