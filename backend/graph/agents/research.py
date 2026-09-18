"""
WebResearchAgent — performs live web research via Tavily plus scholarly
source retrieval (Semantic Scholar, arXiv, Crossref) with provenance labels.
"""

import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from langsmith import traceable
from scholarly.merge import (
    collect_scholarly_results,
    merge_scholarly_and_web_results,
)

from graph.state import ReportState, without_parallel_fact_results
from utils.clients import get_tavily_client
from utils.url_utils import extract_domain

load_dotenv()
logger = logging.getLogger(__name__)


@traceable(name="tavily-web-search", run_type="tool")
def perform_tavily_search(
    query: str,
    max_results: int = 5,
    retries: int = 2,
    stream_updates=None,
    timestamp: str | None = None,
) -> list:
    """Execute a single Tavily search with retry logic"""
    import time

    tavily_client = get_tavily_client()
    if tavily_client is None:
        if stream_updates is not None:
            prefix = f"[{timestamp}] " if timestamp else ""
            stream_updates.append(
                f"{prefix}\u2717 Tavily client unavailable (missing TAVILY_API_KEY)."
            )
        return []

    for attempt in range(retries + 1):
        try:
            results = get_tavily_client().search(
                query=query,
                search_depth="advanced",
                max_results=max_results,
                include_raw_content=False,
            )
            if stream_updates is not None:
                prefix = f"[{timestamp}] " if timestamp else ""
                stream_updates.append(
                    f"{prefix}✓ Tavily returned {len(results.get('results', []))} raw results"
                )

            formatted = []
            for item in results.get("results", []):
                url = item.get("url", "")
                if not url:
                    continue
                formatted.append(
                    {
                        "url": url,
                        "title": item.get("title", "Untitled"),
                        "snippet": item.get("content", "")[:500],
                        "source_domain": extract_domain(url),
                        "relevance_score": min(float(item.get("score", 0.8)), 1.0),
                    }
                )
            return formatted

        except Exception as e:
            if attempt < retries:
                wait = 2**attempt  # 1s, 2s backoff
                logger.warning(
                    f"Tavily search attempt {attempt + 1} failed for '{query[:40]}': {str(e)}. Retrying in {wait}s..."
                )
                if stream_updates is not None:
                    prefix = f"[{timestamp}] " if timestamp else ""
                    stream_updates.append(
                        f"{prefix}\u2717 Tavily search attempt {attempt + 1} failed: {str(e)}"
                    )
                # time.sleep is correct here \u2014 this function runs inside
                # asyncio.to_thread(), not on the event loop. Using asyncio.sleep
                # here would raise RuntimeError (no running event loop in this thread).
                time.sleep(wait)
            else:
                logger.error(
                    f"Tavily search failed after {retries + 1} attempts for '{query[:40]}': {str(e)}"
                )
                if stream_updates is not None:
                    prefix = f"[{timestamp}] " if timestamp else ""
                    stream_updates.append(
                        f"{prefix}\u2717 Tavily search failed after {retries + 1} attempts: {str(e)}"
                    )
                return []
    return []


def build_round_queries(topic: str, coverage_gaps: list[str]) -> list[str]:
    """
    Round-scoped search queries for a gap-driven re-research round (W4).

    Deterministic: each coverage gap becomes one topic-anchored query, capped
    at three per round so one bad round cannot fan out unbounded searches.
    """
    queries = []
    for gap in coverage_gaps:
        cleaned = " ".join(str(gap).split())[:120]
        if cleaned:
            queries.append(f"{topic} {cleaned}")
        if len(queries) == 3:
            break
    return queries


@traceable(name="research-agent", run_type="chain")
def research_node(state: ReportState) -> ReportState:
    """
    LangGraph node for WebResearchAgent.
    Performs multiple Tavily searches and aggregates results.
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    topic = state.get("topic", "")

    # W4 deep-research loop: a gap-tripped round (research_rounds > 0 with
    # recorded coverage gaps) searches gap-scoped queries instead of the
    # standard templates, and APPENDS to research_results so earlier rounds'
    # sources stay citable.
    research_round = state.get("research_rounds") or 0
    coverage_gaps = state.get("coverage_gaps") or []
    is_re_round = research_round > 0 and bool(coverage_gaps)

    if is_re_round:
        queries = build_round_queries(topic, coverage_gaps)
        state["stream_updates"].append(
            f"[{timestamp}] Research Agent → Re-research round {research_round}: "
            f"{len(queries)} gap-scoped quer{'y' if len(queries) == 1 else 'ies'}"
        )
    else:
        state["stream_updates"].append(
            f"[{timestamp}] Research Agent → Starting web research for: {topic}"
        )

    # Debug signal: confirm whether this backend process has a Tavily API key.
    # (We don't print the key value to avoid leaking secrets.)
    key_present = bool(os.getenv("TAVILY_API_KEY"))
    state["stream_updates"].append(
        f"[{timestamp}] Research Agent → Tavily API key present: {'yes' if key_present else 'no'}"
    )

    try:
        # Define search queries
        current_year = datetime.now().year
        queries = [
            f"{topic} latest research and developments {current_year}",
            f"{topic} expert analysis and insights",
            f"{topic} key findings studies and data",
        ]

        all_results = []
        seen_urls = set()

        web_results = []
        for query in queries:
            state["stream_updates"].append(
                f"[{timestamp}] Research Agent → Searching: {query[:50]}..."
            )
            results = perform_tavily_search(
                query, stream_updates=state["stream_updates"], timestamp=timestamp
            )

            # Deduplicate by URL
            for result in results:
                if result["url"] not in seen_urls:
                    seen_urls.add(result["url"])
                    web_results.append(result)

        # Scholarly sources run in parallel (one thread per API) and degrade
        # independently: a source that fails is skipped, research continues
        # with Tavily and the remaining APIs.
        scholarly_results, scholarly_counts = collect_scholarly_results(queries)
        if scholarly_counts:
            counts_text = ", ".join(
                f"{api}: {count}" for api, count in sorted(scholarly_counts.items())
            )
            state["stream_updates"].append(
                f"[{timestamp}] Research Agent → ✓ Scholarly sources: {counts_text}"
            )
        else:
            state["stream_updates"].append(
                f"[{timestamp}] Research Agent → ✗ No scholarly sources available — "
                "continuing with web results only"
            )

        # Provenance-labeled merge: scholarly ahead of web on URL conflicts.
        # merge_scholarly_and_web_results sorts by relevance and caps at 15.
        all_results = merge_scholarly_and_web_results(scholarly_results, web_results)

        # Increment retry counter so supervisor can detect repeated failures
        state["retry_count"] = state.get("retry_count", 0) + 1

        if is_re_round:
            # Re-research rounds APPEND (dedup by URL): sources found in
            # earlier rounds stay citable, so already-written sections keep
            # valid citations and only flagged sections get re-synthesized.
            existing = state.get("research_results", [])
            existing_urls = {r.get("url") for r in existing}
            state["research_results"] = existing + [
                r for r in all_results if r.get("url") not in existing_urls
            ]
        else:
            state["research_results"] = all_results

        if not state.get("research_results"):
            # Total research emptiness (no sources from any round) is fatal.
            # An empty RE-round is soft: earlier rounds' sources remain, the
            # flagged sections re-synthesize from them, and the round cap
            # bounds any further looping.
            state["error"] = (
                "Research returned no results after searching Tavily and the "
                "scholarly APIs (Semantic Scholar, arXiv, Crossref). "
                "Possible causes: invalid TAVILY_API_KEY, network connectivity issue, or proxy blocking outbound requests. "
                "Check backend/.env and network settings."
            )
            state["stream_updates"].append(
                f"[{timestamp}] \u2717 Research Agent \u2192 No results found. Check TAVILY_API_KEY and network. "
                f"Attempt {state['retry_count']} of 3."
            )
        elif is_re_round:
            state["stream_updates"].append(
                f"[{timestamp}] Research Agent → Round {research_round} added "
                f"{len(all_results)} new source(s); {len(state['research_results'])} total"
            )

        # Update status
        state["completed_agents"].append("research")
        final_msg = f"[{timestamp}] Research Agent → Complete: found {len(all_results)} unique sources across {len(queries)} queries"
        state["stream_updates"].append(final_msg)
        logger.info(final_msg)

        return without_parallel_fact_results(state)

    except Exception as e:
        error_msg = f"[{timestamp}] Research Agent → Error: {str(e)}"
        state["stream_updates"].append(error_msg)
        state["error"] = str(e)
        logger.error(error_msg)
        return without_parallel_fact_results(state)
