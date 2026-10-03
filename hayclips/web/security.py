"""Request hardening for the local operator app.

- Host allowlist (127.0.0.1:<port>, localhost:<port>): blocks DNS-rebinding pages from talking to the app.
- Every state-changing request is a POST that must (a) come from the same origin (Origin, or Referer when
  Origin is absent) and (b) carry the per-session CSRF token. GET requests never change state.
- Security headers on every response: strict CSP (self only, no inline script/style), no framing,
  no MIME sniffing, no referrer leakage outside the app.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import PlainTextResponse

SESSION_COOKIE = "hc_session"
CSRF_FIELD = "csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; "
       "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")


def allowed_hosts_for(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


class Csrf:
    """CSRF token = HMAC(secret, session id). The secret lives only in server memory."""

    def __init__(self, secret: bytes | None = None):
        self.secret = secret or secrets.token_bytes(32)

    def token(self, session_id: str) -> str:
        return hmac.new(self.secret, session_id.encode(), hashlib.sha256).hexdigest()

    def valid(self, session_id: str | None, token: str | None) -> bool:
        if not session_id or not token:
            return False
        return hmac.compare_digest(self.token(session_id), token)


def _valid_sid(sid: str | None) -> bool:
    return bool(sid) and len(sid) == 43 and all(ch.isalnum() or ch in "-_" for ch in sid)


def session_id(request: Request) -> str:
    """The session id for CSRF tokens: the cookie, or the new one the middleware is about to set."""
    sid = request.cookies.get(SESSION_COOKIE)
    return sid if _valid_sid(sid) else request.scope["hc_new_session"]


def _same_origin(request: Request, host: str) -> bool:
    origin = request.headers.get("origin")
    if origin:
        return origin != "null" and urlsplit(origin).netloc == host and urlsplit(origin).scheme in ("http", "https")
    referer = request.headers.get("referer")
    if referer:
        return urlsplit(referer).netloc == host
    return False


MAX_FORM_BYTES = 1_000_000
# Raw-body uploads stream straight to disk in the endpoint (never buffered here); the CSRF token comes
# in the X-CSRF-Token header instead of the form.
RAW_UPLOAD_PATHS = {"/new/upload", "/brand/logo"}


class SecurityMiddleware:
    """Pure ASGI middleware: the request body is read once for the CSRF check and replayed to the app
    (BaseHTTPMiddleware would consume the form before the endpoint sees it)."""

    def __init__(self, app, *, allowed_hosts: set[str], csrf: Csrf):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.csrf = csrf

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)
        host = request.headers.get("host", "")
        if host not in self.allowed_hosts:
            return await self._plain(scope, receive, send, "unknown host", 421)
        method = scope["method"]
        if method not in SAFE_METHODS:
            if method != "POST":
                return await self._plain(scope, receive, send, "method not allowed", 405)
            if not _same_origin(request, host):
                return await self._plain(scope, receive, send, "cross-origin request refused", 403)
            if scope["path"] in RAW_UPLOAD_PATHS:
                if not self.csrf.valid(request.cookies.get(SESSION_COOKIE), request.headers.get("x-csrf-token")):
                    return await self._plain(scope, receive, send, "missing or invalid CSRF token; reload the page", 403)
                return await self.app(scope, receive, self._send_with_headers(scope, send, None))
            body, more = b"", True
            while more:
                msg = await receive()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
                if len(body) > MAX_FORM_BYTES:
                    return await self._plain(scope, receive, send, "request too large", 413)

            def replay_factory():
                sent = False

                async def replay():
                    nonlocal sent
                    if sent:
                        return {"type": "http.disconnect"}
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return replay

            form = await Request(scope, replay_factory()).form()
            if not self.csrf.valid(request.cookies.get(SESSION_COOKIE), form.get(CSRF_FIELD)):
                return await self._plain(scope, receive, send, "missing or invalid CSRF token; reload the page", 403)
            receive = replay_factory()
        new = None if _valid_sid(request.cookies.get(SESSION_COOKIE)) else secrets.token_urlsafe(32)
        scope["hc_new_session"] = new

        await self.app(scope, receive, self._send_with_headers(scope, send, new))

    @staticmethod
    def _send_with_headers(scope, send, new):
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                _apply_headers(headers)
                if new:
                    headers.append("set-cookie", f"{SESSION_COOKIE}={new}; HttpOnly; SameSite=Strict; Path=/")
            await send(message)
        return send_with_headers

    async def _plain(self, scope, receive, send, text, status):
        response = PlainTextResponse(text, status_code=status)
        _apply_headers(response.headers)
        await response(scope, receive, send)


def _apply_headers(headers) -> None:
    headers["Content-Security-Policy"] = CSP
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    headers["Referrer-Policy"] = "same-origin"
    headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if "cache-control" not in headers:
        headers["Cache-Control"] = "no-store"
