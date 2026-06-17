"""Slowapi rate-limiter singleton for Mnemos API (T-AUTH, ADR-0014).

Imported by both ``mnemos.api.auth`` (for endpoint decorators) and
``mnemos.api.main`` (to set ``app.state.limiter`` and register the
exception handler).  A single object ensures the in-process counter state
is shared.
"""

from __future__ import annotations

from slowapi import Limiter
from starlette.requests import Request


def _rate_key(request: Request) -> str:
    """Key function for slowapi.

    Uses ``X-Forwarded-For`` only when ``api.trusted_proxies`` is explicitly
    configured (ADR-0014 §rate-limiting).  Falls back to the ASGI-level
    ``request.client.host``.
    """
    api_config = getattr(getattr(request.app, "state", None), "api_config", None)
    if api_config is not None and api_config.trusted_proxies:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


limiter: Limiter = Limiter(key_func=_rate_key)
