from __future__ import annotations

import base64
import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from .config import settings


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, exempt_paths: set[str] | None = None) -> None:
        super().__init__(app)
        self.exempt_paths = exempt_paths or set()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in self.exempt_paths:
            return await call_next(request)
        if _authorized(request.headers.get("authorization")):
            return await call_next(request)
        return Response(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="BDSH Data Platform"'},
        )


def _authorized(header: str | None) -> bool:
    if not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header.removeprefix("Basic ").strip()).decode("utf-8")
    except Exception:
        return False
    username, separator, password = decoded.partition(":")
    if not separator:
        return False
    return secrets.compare_digest(username, settings.import_auth_username) and secrets.compare_digest(
        password,
        settings.import_auth_password,
    )
