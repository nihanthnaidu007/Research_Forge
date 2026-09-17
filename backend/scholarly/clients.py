"""
Scholarly API clients: Semantic Scholar Graph API, arXiv, Crossref.

Each client returns results in the research-agent result shape plus
provenance metadata (``source_type``/``source_api``) and persistent
identifiers (S2 paper ID, arXiv ID, DOI) so citations stay traceable to the
source that actually produced them.

Shared behavior:
- every request has a timeout;
- transient failures (429 / 5xx / network errors) are retried with
  exponential backoff;
- each API gets a thread-safe rate limiter (Semantic Scholar ≈1 RPS
  unauthenticated, arXiv's polite 1-request-per-3-seconds, Crossref ≈1 RPS);
- the Semantic Scholar API key (env ``SEMANTIC_SCHOLAR_API_KEY``) is optional
  — unauthenticated access works and is rate-limited more aggressively;
- Crossref contact email (env ``CROSSREF_CONTACT_EMAIL``) is optional and
  puts requests into Crossref's faster "polite pool".

Clients raise :class:`ScholarlyAPIError` after exhausting retries — callers
degrade gracefully and continue with the remaining sources.
"""

import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Timeouts and retries. Tests monkeypatch these constants / time.sleep.
REQUEST_TIMEOUT_SECONDS = 10.0
RETRY_BACKOFF_SECONDS = 1.0
MAX_RETRIES = 2  # initial attempt + 2 retries

# Polite-pool spacing per API (seconds between consecutive requests).
S2_MIN_INTERVAL = 1.0
ARXIV_MIN_INTERVAL = 3.0
CROSSREF_MIN_INTERVAL = 1.0

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
ARXIV_API_URL = "http://export.arxiv.org/api/query"
CROSSREF_API_URL = "https://api.crossref.org/works"

ARXIV_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

_USER_AGENT = "ResearchForge/1.0 (scholarly research tool)"


class ScholarlyAPIError(RuntimeError):
    """A scholarly API stayed unreachable or kept failing after retries."""


class RateLimiter:
    """Thread-safe minimum-interval limiter between calls to one API."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._last_call: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Block until at least ``min_interval`` has passed since the last call."""
        with self._lock:
            now = time.monotonic()
            if self._last_call is not None:
                remaining = self.min_interval - (now - self._last_call)
                if remaining > 0:
                    time.sleep(remaining)
            self._last_call = time.monotonic()


# One limiter per API, shared across all calls in this process (the graph
# runs agents in worker threads, so the limiter must be thread-safe).
_s2_limiter = RateLimiter(S2_MIN_INTERVAL)
_arxiv_limiter = RateLimiter(ARXIV_MIN_INTERVAL)
_crossref_limiter = RateLimiter(CROSSREF_MIN_INTERVAL)


def _backoff(attempt: int) -> float:
    return RETRY_BACKOFF_SECONDS * (2**attempt)


def _request_with_retry(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    limiter: RateLimiter,
) -> requests.Response:
    """
    GET ``url`` with the shared timeout/retry/rate-limit behavior.

    Retries 429 and 5xx responses and network-level errors with exponential
    backoff; other 4xx responses fail immediately (they are not transient).
    """
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        limiter.wait()
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 429 or response.status_code >= 500:
                last_error = ScholarlyAPIError(
                    f"{url} returned HTTP {response.status_code}"
                )
                logger.warning(
                    "Scholarly API %s returned HTTP %d (attempt %d/%d)",
                    url,
                    response.status_code,
                    attempt + 1,
                    MAX_RETRIES + 1,
                )
            else:
                response.raise_for_status()
                return response
        except requests.exceptions.RequestException as exc:
            last_error = exc
            logger.warning(
                "Scholarly API %s request failed (attempt %d/%d): %s",
                url,
                attempt + 1,
                MAX_RETRIES + 1,
                exc,
            )
        if attempt < MAX_RETRIES:
            time.sleep(_backoff(attempt))
    assert last_error is not None  # MAX_RETRIES >= 0 guarantees at least one
    raise ScholarlyAPIError(
        f"{url} failed after {MAX_RETRIES + 1} attempts: {last_error}"
    ) from last_error


def _position_relevance(index: int) -> float:
    """
    Deterministic relevance from API result rank.

    The scholarly APIs return their own best-first ordering but no
    cross-comparable numeric score, so rank maps onto [0.5, 1.0].
    """
    return max(0.5, 1.0 - 0.05 * index)


def _result(
    *,
    source_api: str,
    url: str,
    title: str,
    snippet: str,
    index: int,
    authors: list[str],
    year: int | None,
    venue: str,
    persistent_ids: dict[str, str],
) -> dict[str, Any]:
    return {
        "url": url,
        "title": (title or "Untitled").strip(),
        "snippet": (snippet or "")[:500],
        "source_domain": re.sub(r"^https?://([^/]+).*$", r"\1", url) if url else "",
        "relevance_score": _position_relevance(index),
        "source_type": "scholarly",
        "source_api": source_api,
        "authors": authors,
        "year": year,
        "venue": (venue or "").strip(),
        "persistent_ids": persistent_ids,
    }


def search_semantic_scholar(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """
    Search the Semantic Scholar Graph API.

    Works unauthenticated (shared rate-limited pool); the optional free API
    key (env ``SEMANTIC_SCHOLAR_API_KEY``) is sent as ``x-api-key``.
    """
    headers = {"User-Agent": _USER_AGENT}
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key

    response = _request_with_retry(
        S2_SEARCH_URL,
        params={
            "query": query,
            "limit": max(1, min(max_results, 20)),
            "fields": "title,abstract,year,venue,authors,externalIds,url",
        },
        headers=headers,
        limiter=_s2_limiter,
    )
    payload = response.json()

    results: list[dict[str, Any]] = []
    for index, paper in enumerate(payload.get("data") or []):
        paper_id = paper.get("paperId") or ""
        if not paper_id:
            continue  # no persistent identity — citation would be untraceable
        external_ids = paper.get("externalIds") or {}
        persistent: dict[str, str] = {"s2_paper_id": paper_id}
        if external_ids.get("DOI"):
            persistent["doi"] = external_ids["DOI"]
        if external_ids.get("ArXiv"):
            persistent["arxiv_id"] = external_ids["ArXiv"]
        results.append(
            _result(
                source_api="semantic_scholar",
                url=paper.get("url")
                or f"https://www.semanticscholar.org/paper/{paper_id}",
                title=paper.get("title") or "",
                snippet=paper.get("abstract") or "",
                index=index,
                authors=[
                    a.get("name", "")
                    for a in (paper.get("authors") or [])
                    if a.get("name")
                ],
                year=paper.get("year"),
                venue=paper.get("venue") or "",
                persistent_ids=persistent,
            )
        )
    return results


def search_arxiv(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search the arXiv Atom API (polite pool: 1 request per 3 seconds)."""
    response = _request_with_retry(
        ARXIV_API_URL,
        params={
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max(1, min(max_results, 20)),
        },
        headers={"User-Agent": _USER_AGENT},
        limiter=_arxiv_limiter,
    )
    feed = ET.fromstring(response.text)

    results: list[dict[str, Any]] = []
    for index, entry in enumerate(feed.findall("atom:entry", ARXIV_ATOM_NS)):
        entry_id = (entry.findtext("atom:id", "", ARXIV_ATOM_NS) or "").strip()
        arxiv_match = re.search(r"arxiv\.org/abs/([^\s?#]+)", entry_id)
        if not arxiv_match:
            continue  # cannot identify the paper persistently — skip
        arxiv_id = arxiv_match.group(1)
        published = entry.findtext("atom:published", "", ARXIV_ATOM_NS) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        authors = [
            (a.findtext("atom:name", "", ARXIV_ATOM_NS) or "").strip()
            for a in entry.findall("atom:author", ARXIV_ATOM_NS)
        ]
        results.append(
            _result(
                source_api="arxiv",
                url=f"https://arxiv.org/abs/{arxiv_id}",
                title=" ".join(
                    (entry.findtext("atom:title", "", ARXIV_ATOM_NS) or "").split()
                ),
                snippet=entry.findtext("atom:summary", "", ARXIV_ATOM_NS) or "",
                index=index,
                authors=[name for name in authors if name],
                year=year,
                venue="arXiv",
                persistent_ids={"arxiv_id": arxiv_id},
            )
        )
    return results


def search_crossref(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """
    Search the Crossref works API.

    ``CROSSREF_CONTACT_EMAIL`` (optional) adds the ``mailto`` polite-pool
    parameter, which Crossref rewards with a more reliable rate allowance.
    """
    params: dict[str, Any] = {
        "query": query,
        "rows": max(1, min(max_results, 20)),
        "select": "DOI,title,abstract,author,issued,container-title,URL",
    }
    contact = os.getenv("CROSSREF_CONTACT_EMAIL", "").strip()
    if contact:
        params["mailto"] = contact

    user_agent = (
        f"{_USER_AGENT} (mailto:{contact})" if contact else _USER_AGENT
    )
    response = _request_with_retry(
        CROSSREF_API_URL,
        params=params,
        headers={"User-Agent": user_agent},
        limiter=_crossref_limiter,
    )
    items = (response.json().get("message") or {}).get("items") or []

    results: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        doi = (item.get("DOI") or "").strip()
        if not doi:
            continue
        titles = item.get("title") or []
        issued = (item.get("issued") or {}).get("date-parts") or []
        year = issued[0][0] if issued and issued[0] and issued[0][0] else None
        container = item.get("container-title") or []
        results.append(
            _result(
                source_api="crossref",
                url=item.get("URL") or f"https://doi.org/{doi}",
                title=(titles[0] if titles else "") or "",
                snippet=item.get("abstract") or "",
                index=index,
                authors=[
                    " ".join(
                        part for part in (a.get("given"), a.get("family")) if part
                    ).strip()
                    for a in (item.get("author") or [])
                    if a.get("family") or a.get("given")
                ],
                year=year,
                venue=container[0] if container else "",
                persistent_ids={"doi": doi},
            )
        )
    return results
