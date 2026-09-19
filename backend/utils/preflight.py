"""
Run-start LLM provider preflight.

A doomed run is worse than no run: when the configured provider credential is
missing or rejected, every agent degrades into fallback content (the 01:05
log showed synthesis 401s repeated across every section) and the run finishes
as garbage. verify_llm_provider() makes one cheap provider call before the
graph starts and raises LLMProviderUnavailableError with a user-readable
message so the runner can fail the run immediately.

The success result is cached for LLM_PREFLIGHT_TTL_SECONDS (default 300) so
concurrent runs don't each pay for a verification call. The probe call is
capped at a handful of tokens and never recorded against a run budget — it
runs before any session context is set.
"""

import logging
import os
import threading
import time

from utils.clients import get_openai_client, llm_model, llm_provider

logger = logging.getLogger(__name__)

# Minimal probe: enough for one short completion; the content is discarded.
PREFLIGHT_PROBE_MAX_TOKENS = 16

_DEFAULT_PREFLIGHT_TTL_SECONDS = 300.0

_verify_lock = threading.Lock()
_last_verified_at: float | None = None


class LLMProviderUnavailableError(RuntimeError):
    """The configured LLM provider is unusable (missing or invalid credential)."""


def _preflight_ttl() -> float:
    raw = os.getenv("LLM_PREFLIGHT_TTL_SECONDS", "").strip()
    if raw:
        try:
            # Negative values disable the preflight (verify returns
            # immediately) — the test suite uses -1 to guarantee no test
            # ever reaches a real provider.
            return float(raw)
        except ValueError:
            logger.warning(
                f"Invalid LLM_PREFLIGHT_TTL_SECONDS={raw!r} — using default "
                f"{_DEFAULT_PREFLIGHT_TTL_SECONDS:.0f}s"
            )
    return _DEFAULT_PREFLIGHT_TTL_SECONDS


def reset_preflight_cache() -> None:
    """Test seam: clear the cached verification result."""
    global _last_verified_at
    with _verify_lock:
        _last_verified_at = None


def _brief_error(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    detail = str(error)[:160]
    prefix = f"HTTP {status}, " if status else ""
    return f"{prefix}{detail}"


def verify_llm_provider(force: bool = False) -> None:
    """
    Verify the configured LLM provider is usable, or raise
    LLMProviderUnavailableError with a user-readable message.

    Cheap by design: a missing key fails without any network call; otherwise
    one minimal chat completion proves the credential + model are accepted.
    A passing result is cached for LLM_PREFLIGHT_TTL_SECONDS. A negative TTL
    disables the check entirely — the test suite sets it so no test ever
    reaches a real provider.
    """
    global _last_verified_at

    ttl = _preflight_ttl()
    if ttl < 0:
        return

    if not force:
        with _verify_lock:
            if (
                _last_verified_at is not None
                and time.monotonic() - _last_verified_at < ttl
            ):
                return

    provider = llm_provider()
    key_var = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    if not os.getenv(key_var, "").strip():
        raise LLMProviderUnavailableError(
            f"LLM provider unavailable: {key_var} is missing — set it in "
            f"backend/.env (LLM_PROVIDER={provider})"
        )

    model = llm_model()
    try:
        get_openai_client().chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_completion_tokens=PREFLIGHT_PROBE_MAX_TOKENS,
        )
    except Exception as e:
        raise LLMProviderUnavailableError(
            f"LLM provider unavailable: {key_var} rejected the preflight call "
            f"(model={model}): {_brief_error(e)}"
        ) from e

    with _verify_lock:
        _last_verified_at = time.monotonic()
    logger.info(
        "LLM provider preflight passed — provider=%s model=%s", provider, model
    )
