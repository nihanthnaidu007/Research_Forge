"""
Scholarly + web result merging with provenance labels.

Pure functions (plus a parallel collector) used by the research agent to
combine Semantic Scholar / arXiv / Crossref results with Tavily web results.
Every merged result carries a provenance label so downstream citations can
say where a claim actually came from.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from scholarly.clients import (
    search_arxiv,
    search_crossref,
    search_semantic_scholar,
)

logger = logging.getLogger(__name__)

# Source registry: monkeypatched in tests to simulate per-source failures.
SCHOLARLY_SOURCES: dict[str, Callable[[str, int], list[dict[str, Any]]]] = {
    "semantic_scholar": search_semantic_scholar,
    "arxiv": search_arxiv,
    "crossref": search_crossref,
}

MAX_MERGED_RESULTS = 15


def label_web_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a Tavily result with web provenance labels applied."""
    labeled = dict(result)
    labeled.setdefault("source_type", "web")
    labeled.setdefault("source_api", "tavily")
    labeled.setdefault("authors", [])
    labeled.setdefault("year", None)
    labeled.setdefault("venue", "")
    labeled.setdefault("persistent_ids", {})
    return labeled


def merge_scholarly_and_web_results(
    scholarly_results: list[dict[str, Any]],
    web_results: list[dict[str, Any]],
    limit: int = MAX_MERGED_RESULTS,
) -> list[dict[str, Any]]:
    """
    Merge scholarly and web results into one provenance-labeled list.

    Pure function. Deduplicates by URL (first occurrence wins — scholarly
    sources are passed in ahead of web results), sorts by relevance score,
    and truncates to ``limit``.
    """
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for result in scholarly_results:
        labeled = label_web_result({**result, "source_type": "scholarly"})
        url = labeled.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(labeled)

    for result in web_results:
        labeled = label_web_result(result)
        url = labeled.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            merged.append(labeled)

    merged.sort(key=lambda r: r.get("relevance_score", 0), reverse=True)
    return merged[:limit]


def collect_scholarly_results(
    queries: list[str],
    max_results_per_query: int = 3,
    source_overrides: dict[str, Callable[[str, int], list[dict[str, Any]]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    Query every scholarly source across ``queries`` in parallel and merge.

    Per-source graceful degradation: a source that raises (unreachable,
    rate-limited, bad key) is skipped with an error log and the remaining
    sources still contribute — research never fails because one API is down.

    Returns ``(results, per_source_counts)`` where counts only include
    sources that answered.
    """
    sources = source_overrides if source_overrides is not None else SCHOLARLY_SOURCES
    results: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    def run_source(source_api: str) -> tuple[str, list[dict[str, Any]], Exception | None]:
        search = sources[source_api]
        source_results: list[dict[str, Any]] = []
        try:
            for query in queries:
                for result in search(query, max_results_per_query):
                    if result.get("url") and result["url"] not in {
                        r["url"] for r in source_results
                    }:
                        source_results.append(result)
            return source_api, source_results, None
        except Exception as exc:  # degradation: keep the other sources
            return source_api, source_results, exc

    with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
        for source_api, source_results, error in pool.map(run_source, list(sources)):
            if error is not None:
                logger.error(
                    "Scholarly source %s is unavailable — continuing without it: %s",
                    source_api,
                    error,
                )
                continue
            results.extend(source_results)
            counts[source_api] = len(source_results)

    return results, counts
