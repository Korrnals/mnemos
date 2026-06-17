"""FastAPI router for API authentication (T-AUTH, ADR-0014).

Endpoints (all mounted under ``/auth`` via ``main.py``):

  POST  /auth/login   — Phase 1: present bearer token → challenge or session
  POST  /auth/verify  — Phase 2: present TOTP code → session + cookie
  POST  /auth/logout  — Invalidate current session
  GET   /auth/me      — Return session / token metadata

TOTP secrets are encrypted at rest with ``cryptography.Fernet`` keyed from
``api.totp_master_key`` (env-only, ``MNEMOS_API__TOTP_MASTER_KEY``).
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from mnemos.api.auth_store import AuthStore
from mnemos.api.rate_limit import limiter
from mnemos.config import load_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Pydantic I/O models ───────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    token: str


class VerifyRequest(BaseModel):
    challenge_id: str
    code: str


# ── Crypto helpers ────────────────────────────────────────────────────────────


def _fernet(master_key: str) -> Fernet:
    """Derive a Fernet key from the master key string via SHA-256."""
    key_bytes = hashlib.sha256(master_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_totp_secret(totp_secret: str, master_key: str) -> bytes:
    """Encrypt a TOTP secret string for at-rest storage."""
    return _fernet(master_key).encrypt(totp_secret.encode())


def decrypt_totp_secret(encrypted: bytes, master_key: str) -> str | None:
    """Decrypt a TOTP secret.  Returns ``None`` on key-mismatch / corruption."""
    try:
        return _fernet(master_key).decrypt(encrypted).decode()
    except InvalidToken:
        return None


# ── Dependency helpers ────────────────────────────────────────────────────────


def _get_auth_store(request: Request) -> AuthStore:
    store: AuthStore | None = getattr(request.app.state, "auth_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Auth service unavailable")
    return store


def _get_client_ip(request: Request) -> str:
    client = request.client
    return client.host if client else "unknown"


def _session_from_request(request: Request) -> str | None:
    """Extract the session token from Bearer header or cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.cookies.get("mnemos_session")


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    payload: LoginRequest,
) -> dict[str, Any]:
    """Phase 1 of authentication.

    Present a ``mnk_`` bearer token.  If TOTP is enrolled the response
    contains a ``challenge_id``; otherwise a session token is issued
    immediately (loopback-only / TOTP-disabled mode).
    """
    if not payload.token.startswith("mnk_"):
        raise HTTPException(status_code=401, detail="Invalid token format")

    sha256 = hashlib.sha256(payload.token.encode()).hexdigest()
    store = _get_auth_store(request)
    token_row = store.get_token_by_hash(sha256)

    if token_row is None or not store.is_token_active(token_row):
        # Constant-time-ish rejection — no timing oracle on hash vs. active check.
        raise HTTPException(status_code=401, detail="Invalid or inactive token")

    token_id = str(token_row["token_id"])
    settings = load_settings().api

    encrypted_blob = token_row.get("totp_secret_encrypted")
    if encrypted_blob is not None:
        # TOTP enrolled — issue challenge
        challenge_id = store.create_challenge(token_id)
        return {"challenge_id": challenge_id, "ttl_sec": 120}

    # No TOTP — issue session directly (loopback / auth-only mode)
    client_ip = _get_client_ip(request) if settings.session_pin_ip else None
    session_plaintext, expires_at = store.create_session(
        token_id=token_id,
        ttl_sec=settings.session_ttl_sec,
        client_ip=client_ip,
    )
    store.reset_failures(token_id)
    logger.info("auth.login: session issued (no TOTP) for token_id=[REDACTED]")
    return {"session": session_plaintext, "expires_at": expires_at}


@router.post("/verify")
@limiter.limit("5/minute")
async def verify(
    request: Request,
    payload: VerifyRequest,
    response: Response,
) -> dict[str, Any]:
    """Phase 2 of authentication.

    Present the ``challenge_id`` from ``/login`` and a 6-digit TOTP code.
    On success: issues a session token and sets the ``mnemos_session`` cookie.
    """
    store = _get_auth_store(request)
    settings = load_settings().api

    challenge = store.get_challenge(payload.challenge_id)
    if challenge is None or not store.is_challenge_valid(challenge):
        raise HTTPException(status_code=401, detail="Invalid or expired challenge")

    token_id = str(challenge["token_id"])
    token_row = store.get_token_by_id(token_id)
    if token_row is None or not store.is_token_active(token_row):
        store.invalidate_challenge(payload.challenge_id)
        raise HTTPException(status_code=401, detail="Token inactive")

    # Decrypt TOTP secret
    encrypted_blob = token_row.get("totp_secret_encrypted")
    if not isinstance(encrypted_blob, bytes):
        raise HTTPException(status_code=400, detail="TOTP not enrolled for this token")

    master_key = settings.totp_master_key.get_secret_value()
    if not master_key:
        raise HTTPException(status_code=500, detail="TOTP master key not configured")

    totp_secret = decrypt_totp_secret(encrypted_blob, master_key)
    if totp_secret is None:
        logger.error("auth.verify: TOTP decrypt failed for token_id=[REDACTED]")
        raise HTTPException(status_code=500, detail="TOTP configuration error")

    totp = pyotp.TOTP(totp_secret)
    if not totp.verify(payload.code, valid_window=1):
        attempts = store.increment_challenge_attempts(payload.challenge_id)
        if attempts >= 5:
            store.invalidate_challenge(payload.challenge_id)
        totp_failures = store.increment_totp_failure(token_id)
        if totp_failures >= 3:
            logger.warning("auth.verify: TOTP brute-force lockout (token=[REDACTED])")
        raise HTTPException(status_code=401, detail="Invalid TOTP code")

    # Success
    store.invalidate_challenge(payload.challenge_id)
    store.reset_failures(token_id)

    client_ip = _get_client_ip(request) if settings.session_pin_ip else None
    session_plaintext, expires_at = store.create_session(
        token_id=token_id,
        ttl_sec=settings.session_ttl_sec,
        client_ip=client_ip,
    )

    is_secure = settings.behind_tls_proxy
    response.set_cookie(
        key="mnemos_session",
        value=session_plaintext,
        httponly=True,
        secure=is_secure,
        samesite="strict",
    )
    logger.info("auth.verify: session issued for token_id=[REDACTED]")
    return {"session": session_plaintext, "expires_at": expires_at}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    """Invalidate the current session and clear the session cookie."""
    session_token = _session_from_request(request)
    if session_token:
        store = _get_auth_store(request)
        session_hash = hashlib.sha256(session_token.encode()).hexdigest()
        store.revoke_session(session_hash)
    response.delete_cookie("mnemos_session")
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> dict[str, Any]:
    """Return metadata about the currently authenticated session and token."""
    session_token = _session_from_request(request)
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    store = _get_auth_store(request)
    session_hash = hashlib.sha256(session_token.encode()).hexdigest()
    session = store.get_session_by_hash(session_hash)
    if not session:
        raise HTTPException(status_code=401, detail="Session not found")

    token_id = str(session["token_id"])
    token_row = store.get_token_by_id(token_id)
    if not token_row:
        raise HTTPException(status_code=401, detail="Token not found")

    return {
        "token_id": token_id,
        "totp": token_row.get("totp_secret_encrypted") is not None,
        "expires_at": session.get("expires_at"),
    }
