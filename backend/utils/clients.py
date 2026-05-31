"""
Shared API client singletons for ResearchForge.
Clients are initialized lazily on first use, not at import time.
This ensures a single HTTP connection pool per provider across all agents.
validate_env_vars() is called at server startup to fail fast with a clear
message if required keys are absent.
"""

import logging
import os

from openai import OpenAI
from tavily import TavilyClient

logger = logging.getLogger(__name__)

_openai_client: OpenAI | None = None
_tavily_client: TavilyClient | None = None

REQUIRED_ENV_VARS = {
    "OPENAI_API_KEY": "OpenAI API key — required for all LLM calls",
    "TAVILY_API_KEY": "Tavily API key — required for web research",
    "DATABASE_URL": "PostgreSQL connection string — required for session persistence",
    "CORS_ORIGINS": (
        "Allowed CORS origins — comma-separated list of frontend URLs, "
        "or '*' to allow all origins (development only)"
    ),
}


def get_openai_client() -> OpenAI:
    """Return the shared OpenAI client, creating it on first call."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        logger.info("OpenAI client initialized")
    return _openai_client


def get_tavily_client() -> TavilyClient:
    """Return the shared Tavily client, creating it on first call."""
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
        logger.info("Tavily client initialized")
    return _tavily_client


def validate_env_vars() -> None:
    """
    Called at server startup. Raises RuntimeError with a specific message
    listing every missing variable if any required env var is absent or empty.
    """
    missing = []
    for var, description in REQUIRED_ENV_VARS.items():
        if not os.getenv(var, "").strip():
            missing.append(f"  {var}: {description}")

    if missing:
        raise RuntimeError(
            "ResearchForge cannot start — missing required environment variables:\n\n"
            + "\n".join(missing)
            + "\n\nAdd these to backend/.env and restart the server."
        )

    logger.info("Environment validation passed — all required vars present")
