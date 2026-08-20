from __future__ import annotations

from webapp import server


_INSTALLED = False


def step92_health() -> dict[str, object]:
    return {
        "ok": True,
        "release": "step92",
        "reply_role": True,
        "privacy_safe_role_command": True,
        "worker_workplaces": True,
        "workplace_routing": True,
        "compact_bot_menu": True,
    }


def install_step92_probe() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if not any(getattr(route, "path", "") == "/api/step92/health" for route in server.app.routes):
        server.app.add_api_route(
            "/api/step92/health",
            step92_health,
            methods=["GET"],
            include_in_schema=False,
        )
    _INSTALLED = True
