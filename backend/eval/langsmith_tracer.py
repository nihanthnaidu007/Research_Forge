"""
LangSmith Tracing Configuration for ResearchForge
Provides tracing for all agent nodes and LLM calls in the multi-agent graph.
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def get_langsmith_config(run_name: str, thread_id: Optional[str] = None, tags: Optional[list] = None) -> dict:
    """
    Return LangSmith RunnableConfig for graph.invoke().
    LangSmith automatically picks up tracing from environment variables
    when LANGCHAIN_TRACING_V2=true is set.

    Args:
        run_name: Human-readable name for this run (e.g. "research-report-ai-agents-in-healthcare")
        thread_id: Optional session/thread identifier for grouping traces
        tags: Optional list of tags to attach to the trace

    Returns:
        dict: RunnableConfig for passing to graph.invoke()
    """
    if not os.getenv("LANGCHAIN_TRACING_V2"):
        return {}

    config = {
        "run_name": run_name,
        "tags": tags or ["research-forge", "multi-agent"],
        "metadata": {
            "project": os.getenv("LANGCHAIN_PROJECT", "Multi-Agent-Research"),
            "version": "1.0.0",
        }
    }

    if thread_id:
        config["metadata"]["thread_id"] = thread_id
        config["configurable"] = {"thread_id": thread_id}

    return config


def is_tracing_enabled() -> bool:
    """Check if LangSmith tracing is configured and enabled"""
    return (
        os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
        and bool(os.getenv("LANGCHAIN_API_KEY"))
    )


def get_trace_url(project: Optional[str] = None) -> str:
    """Return the correct LangSmith project URL for personal workspace"""
    project_name = project or os.getenv("LANGCHAIN_PROJECT", "Multi-Agent-Research")
    return f"https://smith.langchain.com/projects/{project_name}"


def setup_tracing():
    """
    Verify LangSmith tracing environment is configured correctly.
    Call this on application startup.
    """
    if is_tracing_enabled():
        logger.info(f"LangSmith tracing ENABLED — project: {os.getenv('LANGCHAIN_PROJECT')}")
        logger.info(f"Trace URL: {get_trace_url()}")
    else:
        logger.warning(
            "LangSmith tracing DISABLED — set LANGCHAIN_TRACING_V2=true and "
            "LANGCHAIN_API_KEY in .env to enable"
        )
