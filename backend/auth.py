"""
Authentication for ResearchForge.

Two layers, both configured via environment:

1. API key — required on every route except /api/health. Set
   RESEARCHFORGE_API_KEY. Fails closed: when the variable is unset or
   empty, every protected route rejects all requests (503).

2. Session token — issued in the POST /api/run response and required, in
   addition to the API key, for the session SSE stream, outline approval,
   and PDF download. Prevents a guessed session UUID from being used to
   read or approve someone else's report.

Session tokens are stored only as SHA-256 hashes; the plaintext token is
returned exactly once, in the run response.
"""

import asyncio
import hashlib
import os
import secrets

from fastapi import Header, HTTPException, Query, Request

from db import get_session_token_hash

API_KEY_HEADER = "X-API-Key"
SESSION_TOKEN_HEADER = "X-Session-Token"
SESSION_TOKEN_QUERY_PARAM = "session_token"

_API_KEY_ENV_VAR = "RESEARCHFORGE_API_KEY"


def get_configured_api_key() -> str:
    """Read the API key at request time; an unset or empty value fails closed."""
    return os.getenv(_API_KEY_ENV_VAR, "").strip()


def _constant_time_equal(provided: str, expected: str) -> bool:
    return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _extract_api_credential(request: Request) -> str | None:
    """Accept the key via X-API-Key or an Authorization: Bearer header."""
    header_value = request.headers.get(API_KEY_HEADER, "").strip()
    if header_value:
        return header_value
    authorization = request.headers.get("Authorization", "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def require_api_key(request: Request) -> None:
    """FastAPI dependency: reject any request without the configured API key."""
    configured = get_configured_api_key()
    if not configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Authentication is not configured on this server "
                f"({_API_KEY_ENV_VAR} is unset)."
            ),
        )
    provided = _extract_api_credential(request)
    if not provided or not _constant_time_equal(provided, configured):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def generate_session_token() -> str:
    """Generate a high-entropy session token for the run response."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """Hash a session token for storage — the plaintext is never persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def extract_session_token(header_value: str | None, query_value: str | None) -> str | None:
    """
    Prefer the header; fall back to the query parameter for clients that
    cannot set headers (SSE event sources, anchor-based downloads).
    """
    value = (header_value or "").strip() or (query_value or "").strip()
    return value or None


async def enforce_session_ownership(session_id: str, provided_token: str | None) -> None:
    """
    Reject requests that cannot prove ownership of the session.

    401 when no token is presented, 404 when the session does not exist
    (or predates tokens — fail closed), 403 when the token does not match.
    """
    if not provided_token:
        raise HTTPException(
            status_code=401,
            detail=(
                f"Session token required ({SESSION_TOKEN_HEADER} header "
                f"or ?{SESSION_TOKEN_QUERY_PARAM}= query parameter)."
            ),
        )
    stored_hash = await asyncio.to_thread(get_session_token_hash, session_id)
    if stored_hash is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not _constant_time_equal(hash_session_token(provided_token), stored_hash):
        raise HTTPException(status_code=403, detail="Invalid session token for this session.")


async def require_session_ownership(
    session_id: str,
    x_session_token: str | None = Header(default=None, alias=SESSION_TOKEN_HEADER),
    session_token: str | None = Query(default=None),
) -> None:
    """FastAPI dependency for routes carrying session_id in the path."""
    provided = extract_session_token(x_session_token, session_token)
    await enforce_session_ownership(session_id, provided)
