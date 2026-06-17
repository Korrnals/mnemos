"""ASGI auth middleware for Mnemos API (T-AUTH, ADR-0014).

Sits after CORS, before routes.  Logic:

- Bypass list: ``/health``, ``/auth/login``, ``/auth/verify``, ``/docs``,
  ``/redoc``, ``/openapi.json``.  All other paths require a valid session.
- Trust-zone resolution: if ``api.auth_enabled`` is ``False`` AND
  ``api.host`` is a loopback address (``127.0.0.1`` / ``::1`` /
  ``localhost``), the middleware is a no-op (local-desktop scenario, ADR §
  Trust zones).  If ``auth_enabled`` is ``False`` but the configured bind is
  non-loopback, requests are rejected with 401 as defence-in-depth (the
  startup guard in ``lifespan`` normally prevents reaching this branch).
- Session extraction: ``Authorization: Bearer <session>`` takes precedence;
  ``mnemos_session`` cookie is the fallback.
- CSRF: state-changing methods (POST / PUT / DELETE / PATCH) additionally
  require the ``Authorization: Bearer`` header — a cross-origin attacker
  cannot read or set that header on a cookie-only request (ADR §CSRF T3).
- On every authenticated request the session TTL is slid forward and the
  validated session dict is stored on ``request.state.auth_session`` for
  downstream use.

Deviation from ADR letter: the loopback check uses ``api.host`` (the
configured bind address) rather than ``request.client.host``.  Rationale:
Starlette's ``TestClient`` sets ``client.host = "testclient"`` which is not
in ``{"127.0.0.1", "::1"}``; checking the bind address gives the correct
semantics (if the server is configured for loopback, it *is* loopback) and
preserves backward-compatibility for the existing test suite.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from mnemos.api.auth_store import AuthStore
from mnemos.api.client_ip import resolve_client_ip

logger = logging.getLogger(__name__)

_BYPASS_PATHS = frozenset(
    {
        "/health",
        "/auth/login",
        "/auth/verify",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)
_MUTATION_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


def _is_loopback_host(host: str) -> bool:
    if host in {"localhost", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _json_response(body: str, status: int) -> Response:
    return Response(body, status_code=status, media_type="application/json")


class AuthMiddleware(BaseHTTPMiddleware):
    """HTTP middleware enforcing bearer-token / session authentication."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: object) -> Response:
        from collections.abc import Awaitable, Callable

        _call_next: Callable[[Request], Awaitable[Response]] = call_next  # type: ignore[assignment]

        path = request.url.path

        # 1. Bypass list
        if path in _BYPASS_PATHS:
            return await _call_next(request)

        # 2. Load auth config from app state (set in lifespan)
        api_config = getattr(request.app.state, "api_config", None)
        if api_config is None:
            # Fail closed - finding auth-2. Tests that build a bare app
            # MUST attach an api_config to app.state (loopback ApiConfig is
            # fine) so the middleware can make a trust-zone decision.
            logger.error("auth: request rejected - app.state.api_config not initialised")
            return _json_response('{"detail":"Auth not initialised"}', 503)

        # 3. Trust-zone resolution
        if not api_config.auth_enabled:
            if _is_loopback_host(api_config.host):
                # Loopback bind + auth off → local-desktop mode, allow all.
                return await _call_next(request)
            # Non-loopback + auth off → defense-in-depth rejection.
            # The startup guard in lifespan normally prevents this state.
            logger.warning(
                "auth: non-loopback bind (%r) with auth_enabled=False — rejecting request",
                api_config.host,
            )
            return _json_response('{"detail":"Unauthorized"}', 401)

        # 4. Extract session token (Bearer > cookie)
        auth_header = request.headers.get("Authorization", "")
        bearer: str | None = None
        if auth_header.startswith("Bearer "):
            bearer = auth_header[7:]

        session_token = bearer or request.cookies.get("mnemos_session")

        if not session_token:
            return _json_response('{"detail":"Authentication required"}', 401)

        # 5. Validate session
        session_hash = hashlib.sha256(session_token.encode()).hexdigest()
        auth_store: AuthStore | None = getattr(request.app.state, "auth_store", None)
        if auth_store is None:
            return _json_response('{"detail":"Auth service unavailable"}', 503)

        session = auth_store.get_session_by_hash(session_hash)
        # Finding auth-6: when behind a TLS proxy AND the peer is a trusted
        # proxy, derive client IP from the validated X-Forwarded-For header
        # rather than the proxy IP itself. Otherwise the pinned IP would be
        # the proxy IP and pinning would be a no-op against an attacker
        # behind the same proxy.
        if api_config.behind_tls_proxy:
            client_ip: str | None = resolve_client_ip(request, api_config)
        else:
            client = request.client
            client_ip = client.host if client else None

        if session is None or not auth_store.is_session_valid(
            session,
            client_ip=client_ip,
            session_pin_ip=api_config.session_pin_ip,
        ):
            return _json_response('{"detail":"Invalid or expired session"}', 401)

        # 6. CSRF: mutations require the Authorization: Bearer header
        if request.method in _MUTATION_METHODS and not bearer:
            return _json_response(
                '{"detail":"CSRF protection: Authorization header required for mutations"}',
                403,
            )

        # 7. Slide session TTL and expose session to downstream handlers
        auth_store.touch_session(session_hash, api_config.session_ttl_sec)
        request.state.auth_session = session
        request.state.auth_session_hash = session_hash

        return await _call_next(request)
