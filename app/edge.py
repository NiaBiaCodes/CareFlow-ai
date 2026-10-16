"""Restricted ASGI boundary around the SAME application for a development tunnel.

Only payer pages/actions and the two required static assets are reachable. The
clinician UI, approval API, sources, OpenAPI, and database are never forwarded.
"""
import re

from starlette.responses import JSONResponse

from .main import create_app


class PortalBoundary:
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "lifespan": return await self.app(scope, receive, send)
            return
        path, method = scope["path"], scope["method"]
        payer = re.fullmatch(r"/mock-payer/requests/[a-f0-9-]{36}(?:/(draft|submit|state|events))?", path)
        static = method == "GET" and path in {"/static/app.js", "/static/style.css"}
        allowed = payer and method in {"GET", "POST"}
        if not static and not allowed:
            return await JSONResponse({"error": "PORTAL_ROUTE_NOT_EXPOSED"}, status_code=404)(scope, receive, send)
        scope.setdefault("state", {})["portal_only"] = True
        await self.app(scope, receive, send)


app = PortalBoundary(create_app())
