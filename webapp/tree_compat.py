from __future__ import annotations

from webapp import server
from webapp.extensions import CreateAreaPayload, create_area_api

_INSTALLED = False


def install_tree_compat() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    existing = {getattr(route, "path", "") for route in server.app.routes}
    if "/api/extensions/area" not in existing:
        server.app.add_api_route(
            "/api/extensions/area",
            create_area_api,
            methods=["POST"],
            response_model=None,
        )
    _INSTALLED = True


__all__ = ["CreateAreaPayload", "install_tree_compat"]
