from __future__ import annotations

import asyncio
import logging

import uvicorn

from .config import settings
from .db import init_db
from . import db
from .services import control_center
from .services.frontend_runtime import ensure_frontend_runtime_ready
from .main import run_bot

log = logging.getLogger("production_account_runtime")


async def _run_miniapp() -> None:
    # Register additive Mini App routes before Uvicorn starts serving requests.
    # The legacy extension module keeps compatibility endpoints; tree extensions
    # provide the hierarchical interface and its dedicated actions.
    from webapp.extensions import install_extensions
    from webapp.tree_extensions import install_tree_extensions
    from webapp.tree_compat import install_tree_compat
    from webapp.step92_probe import install_step92_probe

    install_extensions()
    install_tree_extensions()
    install_tree_compat()
    install_step92_probe()
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


async def _watchdog_loop() -> None:
    tick = 0
    while True:
        try:
            probe = db.database_probe(1200)
            details = f"db={'ok' if probe.get('ok') else 'error'} {probe.get('latency_ms', 0)}ms"
            control_center.heartbeat("runtime", "ok", "combined process active")
            control_center.heartbeat("watchdog", "ok" if probe.get("ok") else "warning", details)
            tick += 1
            if tick % 5 == 0:
                checkpoint = db.checkpoint_wal()
                if not checkpoint.get("ok") and checkpoint.get("error"):
                    control_center.heartbeat("watchdog", "warning", f"wal checkpoint: {checkpoint.get('error')}")
        except Exception as exc:
            # Watchdog never stops the accounting process. It only records state.
            log.warning("Watchdog: %s", exc)
            try:
                control_center.heartbeat("watchdog", "error", str(exc))
            except Exception:
                pass
        await asyncio.sleep(60)


async def main() -> None:
    settings.require_ready()
    frontend = ensure_frontend_runtime_ready()
    if frontend.changed:
        log.warning("Mini App runtime repaired before startup: %s", frontend.active_asset)
    init_db()
    tasks: list[asyncio.Task] = [asyncio.create_task(_watchdog_loop(), name="watchdog")]
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
