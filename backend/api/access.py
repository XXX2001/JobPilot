"""Local access boundary (W07 / F04, F11).

JobPilot is a single-user local app. CORS restricts which pages may *read*
responses; it does not stop a hostile page from *sending* commands. This
pure-ASGI middleware guards every HTTP request and WebSocket handshake:

1. **Host** must be a loopback name or a host from the allowed origins —
   blocks DNS-rebinding pages that resolve their own name to 127.0.0.1.
2. **State changes** (POST/PUT/PATCH/DELETE under ``/api``) and every
   ``/ws`` handshake must carry the per-installation session secret (the
   ``jobpilot_session`` cookie, or the ``X-JobPilot-Session`` header for local
   scripts), must not come from an untrusted ``Origin``, and must not be marked
   ``Sec-Fetch-Site: cross-site``.

The browser obtains the cookie from ``GET /api/session`` (HttpOnly,
SameSite=Strict, so a cross-site page can neither read it nor have it sent).
The secret lives in ``<DATA_DIR>/.session_secret`` (0600) — never in a URL
or a log line. Remote access stays unsupported: it needs real authentication.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SESSION_COOKIE = "jobpilot_session"
SESSION_HEADER = b"x-jobpilot-session"
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def load_or_create_secret(data_dir: Path) -> str:
    path = data_dir / ".session_secret"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except FileNotFoundError:
        pass
    data_dir.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value)
    return value


def _host_only(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):  # [::1]:8000
        return value[1 : value.find("]")] if "]" in value else value
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _cookie(header: str, name: str) -> str | None:
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


class AccessBoundary:
    def __init__(
        self, app, *, allowed_origins: Iterable[str], extra_hosts: Iterable[str], secret: str
    ):
        self.app = app
        self.origins = {o.rstrip("/") for o in allowed_origins}
        self.hosts = (
            _LOOPBACK
            | {h.lower() for h in extra_hosts if h}
            | {(urlsplit(o).hostname or "").lower() for o in self.origins}
        )
        self.secret = secret

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        path = scope.get("path", "")

        def h(name: bytes) -> str:
            return headers.get(name, b"").decode("latin-1")

        if _host_only(h(b"host")) not in self.hosts:
            return await self._deny(kind, scope, send, 403, "Host not allowed")

        guarded = kind == "websocket" or (
            path.startswith("/api") and scope.get("method") in _UNSAFE
        )
        if guarded:
            origin = h(b"origin").rstrip("/")
            scheme = {"ws": "http", "wss": "https"}.get(scope.get("scheme", "http"), scope.get("scheme", "http"))
            same_origin = origin == f"{scheme}://{h(b'host')}".lower()  # Host validated above
            if origin and origin not in self.origins and not same_origin:
                return await self._deny(kind, scope, send, 403, "Origin not allowed")
            if h(b"sec-fetch-site") == "cross-site":
                return await self._deny(kind, scope, send, 403, "Cross-site request refused")
            token = h(SESSION_HEADER) or _cookie(h(b"cookie"), SESSION_COOKIE) or ""
            if not hmac.compare_digest(token.encode(), self.secret.encode()):
                return await self._deny(kind, scope, send, 401, "Session required; reload the app")
        return await self.app(scope, receive, send)

    @staticmethod
    async def _deny(kind, scope, send, status: int, reason: str) -> None:
        logger.warning("Access boundary refused %s %s: %s", kind, scope.get("path"), reason)
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": reason})
            return
        body = ('{"detail":"%s"}' % reason).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
