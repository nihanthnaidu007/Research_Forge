"""
Shared API client singletons for ResearchForge.
Clients are initialized lazily on first use, not at import time.
This ensures a single HTTP connection pool per provider across all agents.

LLM provider selection (LLM_PROVIDER):
- "anthropic" (default): the OpenAI SDK talks to Anthropic's OpenAI-compatible
  endpoint (base_url https://api.anthropic.com/v1/) with an ANTHROPIC_API_KEY.
  Verified against Anthropic's OpenAI SDK compatibility docs: chat.completions,
  max_completion_tokens, stream, and usage.{prompt,completion,total}_tokens
  are fully supported; response_format is silently ignored (prompts already
  enforce JSON-only output, and parsing is fence-tolerant — utils/llm_utils.py).
- "openai": the original OpenAI path (OPENAI_API_KEY), fully preserved.

Tavily is optional: without TAVILY_API_KEY, get_tavily_client() returns None
and the research agent skips web search (scholarly sources carry the run).
validate_env_vars() is called at server startup to fail fast with a clear
message if required keys are absent.
"""

import logging
import os

from openai import OpenAI
from tavily import TavilyClient

from utils import token_budget

logger = logging.getLogger(__name__)

_openai_client: OpenAI | None = None
_tavily_client: TavilyClient | None = None
_tavily_unavailable_logged = False

# Anthropic's OpenAI-compatible endpoint (verified against Anthropic's
# "OpenAI SDK compatibility" docs, September 2026).
ANTHROPIC_OPENAI_BASE_URL = "https://api.anthropic.com/v1/"

# Per-provider default models. claude-haiku-4-5-20251001 is the verified
# working Anthropic default (claude-sonnet-4-20250514 404s on this account);
# gpt-4o preserves the historical OpenAI default.
_DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_OPENAI_MODEL = "gpt-4o"

_VALID_LLM_PROVIDERS = ("anthropic", "openai")

REQUIRED_ENV_VARS = {
    "DATABASE_URL": "PostgreSQL connection string — required for session persistence",
    "CORS_ORIGINS": (
        "Allowed CORS origins — comma-separated list of frontend URLs, "
        "or '*' to allow all origins (development only)"
    ),
}


def llm_provider() -> str:
    """Configured LLM provider: 'anthropic' (default) or 'openai'."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:
        return "anthropic"
    return provider


def llm_model() -> str:
    """Configured chat model (LLM_MODEL), provider-aware default."""
    model = os.getenv("LLM_MODEL", "").strip()
    if model:
        return model
    return _DEFAULT_OPENAI_MODEL if llm_provider() == "openai" else _DEFAULT_ANTHROPIC_MODEL


def _provider_api_key_var() -> str:
    """Env var holding the credential for the configured provider."""
    return "OPENAI_API_KEY" if llm_provider() == "openai" else "ANTHROPIC_API_KEY"


def get_openai_client() -> OpenAI:
    """
    Return the shared OpenAI SDK client, creating it on first call.

    Name kept deliberately: this returns an OpenAI SDK client for every
    provider. With LLM_PROVIDER=anthropic it points at Anthropic's
    OpenAI-compatible endpoint, so call sites keep reading
    response.choices[0].message.content unchanged.
    """
    global _openai_client
    if _openai_client is None:
        api_key = os.getenv(_provider_api_key_var(), "").strip()
        if not api_key:
            raise RuntimeError(
                f"LLM provider unavailable: {_provider_api_key_var()} is not set "
                f"(LLM_PROVIDER={llm_provider()})"
            )
        if llm_provider() == "anthropic":
            _openai_client = OpenAI(
                api_key=api_key, base_url=ANTHROPIC_OPENAI_BASE_URL
            )
            logger.info(
                "LLM client initialized — provider=anthropic model=%s", llm_model()
            )
        else:
            _openai_client = OpenAI(api_key=api_key)
            logger.info("LLM client initialized — provider=openai model=%s", llm_model())
    return _openai_client


def usage_total_tokens(usage: object) -> int | None:
    """
    Normalize an LLM usage object to total tokens, or None when no
    recognizable token counts are present.

    Handles both shapes: OpenAI's prompt_tokens/completion_tokens/total_tokens
    (what Anthropic's compatibility endpoint returns) and the Anthropic-native
    input_tokens/output_tokens (what native SDK responses and test fixtures
    carry). Centralized so per-run budgets stay correct either way.
    """
    if usage is None:
        return None
    total = getattr(usage, "total_tokens", None)
    if total:
        return int(total)
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if prompt is not None and completion is not None:
        return int(prompt) + int(completion)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if input_tokens is not None and output_tokens is not None:
        return int(input_tokens) + int(output_tokens)
    return None


def chat_completion_with_usage(**kwargs):
    """
    Create a chat completion and record token usage against the active run's
    token budget (see utils/token_budget.py).

    Drop-in replacement for get_openai_client().chat.completions.create():
    same arguments, same response, plus per-run usage accounting. Usage is
    only recorded when a run context is active (set by the graph runners).
    """
    response = get_openai_client().chat.completions.create(**kwargs)
    total_tokens = usage_total_tokens(getattr(response, "usage", None))
    if total_tokens:
        token_budget.record_usage_for_current_session(int(total_tokens))
    return response


def get_tavily_client() -> TavilyClient | None:
    """
    Return the shared Tavily client, or None when TAVILY_API_KEY is not set.

    Tavily is optional: research runs on scholarly sources alone. Returning
    None (instead of raising inside the constructor) lets the research agent
    skip web search with a run-visible note instead of retrying against a
    missing credential.
    """
    global _tavily_client, _tavily_unavailable_logged
    if _tavily_client is None:
        api_key = os.getenv("TAVILY_API_KEY", "").strip()
        if not api_key:
            if not _tavily_unavailable_logged:
                logger.warning(
                    "TAVILY_API_KEY not set — web search disabled; research "
                    "will use scholarly sources only (Semantic Scholar, arXiv, Crossref)"
                )
                _tavily_unavailable_logged = True
            return None
        _tavily_client = TavilyClient(api_key=api_key)
        logger.info("Tavily client initialized")
    return _tavily_client


def validate_env_vars() -> None:
    """
    Called at server startup. Raises RuntimeError with a specific message
    listing every missing variable if any required env var is absent or empty.

    The LLM credential is provider-specific: LLM_PROVIDER=anthropic (default)
    requires ANTHROPIC_API_KEY; LLM_PROVIDER=openai requires OPENAI_API_KEY.
    TAVILY_API_KEY is optional — without it web search is skipped and
    scholarly retrieval carries research runs.
    """
    provider = llm_provider()
    if provider not in _VALID_LLM_PROVIDERS:
        raise RuntimeError(
            "ResearchForge cannot start — LLM_PROVIDER must be one of "
            f"{', '.join(_VALID_LLM_PROVIDERS)} (got {provider!r})."
        )

    missing = []
    for var, description in REQUIRED_ENV_VARS.items():
        if not os.getenv(var, "").strip():
            missing.append(f"  {var}: {description}")

    llm_key_var = _provider_api_key_var()
    if not os.getenv(llm_key_var, "").strip():
        missing.append(
            f"  {llm_key_var}: LLM API key — required for all LLM calls "
            f"(LLM_PROVIDER={provider})"
        )

    if missing:
        raise RuntimeError(
            "ResearchForge cannot start — missing required environment variables:\n\n"
            + "\n".join(missing)
            + "\n\nAdd these to backend/.env and restart the server."
        )

    if not os.getenv("TAVILY_API_KEY", "").strip():
        logger.info(
            "TAVILY_API_KEY not set — web search will be skipped; research "
            "runs will use scholarly sources only"
        )
    logger.info("Environment validation passed — all required vars present")
