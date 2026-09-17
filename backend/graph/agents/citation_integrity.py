"""
Citation-integrity enrichment — pure functions, no LLM calls.

Resolves the scholarly citations in a finished citation list against the
Semantic Scholar graph (existence + citationCount) and Crossref's Retraction
Watch metadata (retraction notices), attaching integrity fields to each
source:

- ``integrity_status``: "verified" (the DOI resolves to a real paper in the
  S2 graph), "unresolved" (S2 answered and the DOI matches no paper — a
  possible fabrication signal), "retracted" (Crossref flags a retraction
  notice), or "unknown" (no DOI, lookup failed, or budget exhausted — never
  a guess).
- ``retracted``: True only on positive Crossref retraction evidence.
- ``citation_count``: S2 citationCount when available, else None.

Degradation contract: research never fails because an integrity API is down.
Any lookup failure leaves the citation at "unknown" / unflagged and the
pipeline continues.

Budget (documented in the W2 PR): one S2 batch request per run covering up
to ``MAX_INTEGRITY_S2_DOIS`` unique DOIs, plus at most
``MAX_CROSSREF_RETRACTION_LOOKUPS`` Crossref retraction GETs per run within
``INTEGRITY_TIME_BUDGET_SECONDS`` wall-clock — so a 40-citation report adds
at most a handful of seconds to the final agent step instead of minutes.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from scholarly.clients import get_crossref_integrity, get_paper_integrity

logger = logging.getLogger(__name__)

INTEGRITY_STATUS_VERIFIED = "verified"
INTEGRITY_STATUS_RETRACTED = "retracted"
INTEGRITY_STATUS_UNRESOLVED = "unresolved"
INTEGRITY_STATUS_UNKNOWN = "unknown"

MAX_INTEGRITY_S2_DOIS = 50
MAX_CROSSREF_RETRACTION_LOOKUPS = 10
INTEGRITY_TIME_BUDGET_SECONDS = 15.0

IntegrityLookup = Callable[[list[str]], dict[str, dict[str, Any] | None]]
RetractionLookup = Callable[[str], bool]
Clock = Callable[[], float]


def _doi_of(source: dict[str, Any]) -> str:
    """The source's DOI from W1 persistent_ids, stripped ('' when absent)."""
    doi = (source.get("persistent_ids") or {}).get("doi") or ""
    return doi.strip()


def unique_dois_in_order(sources: list[dict[str, Any]]) -> list[str]:
    """
    Unique DOIs across sources in first-appearance order.

    DOIs are case-insensitive, so dedup keys on the lowercase form while
    keeping the first-seen spelling as the canonical lookup key.
    """
    dois: list[str] = []
    seen: set[str] = set()
    for source in sources:
        doi = _doi_of(source)
        if doi and doi.lower() not in seen:
            seen.add(doi.lower())
            dois.append(doi)
    return dois


def enrich_citations_with_integrity(
    sources: list[dict[str, Any]],
    *,
    lookup_paper_integrity: IntegrityLookup = get_paper_integrity,
    lookup_crossref_retraction: RetractionLookup = get_crossref_integrity,
    max_s2_dois: int = MAX_INTEGRITY_S2_DOIS,
    max_crossref_lookups: int = MAX_CROSSREF_RETRACTION_LOOKUPS,
    time_budget_seconds: float = INTEGRITY_TIME_BUDGET_SECONDS,
    clock: Clock = time.monotonic,
) -> list[dict[str, Any]]:
    """
    Return enriched *copies* of ``sources`` with citation-integrity fields.

    DOI-keyed merge: every citation whose ``persistent_ids`` carry the same
    DOI receives identical enrichment — one lookup per unique paper, applied
    to all of its citations. Enrichment never appends Source entries, so a
    paper reached by both a publisher URL and a scholarly URL stays one
    citation record.

    Never raises: S2/Crossref failures degrade to ``integrity_status:
    "unknown"`` and the research run continues.
    """
    if not sources:
        return []

    dois = unique_dois_in_order(sources)
    batch_dois = dois[:max_s2_dois]
    if len(dois) > max_s2_dois:
        logger.warning(
            "Citation-integrity budget: %d unique DOIs exceeds the per-run "
            "S2 batch cap (%d); the remainder stay unknown.",
            len(dois),
            max_s2_dois,
        )

    # Existence + citation count: one batched S2 request for the whole run.
    papers: dict[str, dict[str, Any] | None] = {}
    if batch_dois:
        try:
            papers = {
                doi.lower(): paper
                for doi, paper in lookup_paper_integrity(batch_dois).items()
            }
        except Exception as exc:  # degradation — never fail the run
            logger.warning(
                "S2 paper-batch integrity lookup failed: %s", exc
            )

    # Retraction: per-DOI Crossref GETs under both a count cap and a
    # wall-clock budget (first-appearance order wins the budget). Each
    # lookup degrades independently — one failure never fails the run.
    retracted_by_doi: dict[str, bool] = {}
    checked = 0
    deadline = clock() + time_budget_seconds
    for doi in batch_dois:
        if checked >= max_crossref_lookups or clock() >= deadline:
            break
        try:
            retracted_by_doi[doi.lower()] = bool(lookup_crossref_retraction(doi))
        except Exception as exc:  # degradation — keep the other DOIs flowing
            logger.warning(
                "Crossref retraction lookup failed for %s: %s", doi, exc
            )
        checked += 1
    if len(batch_dois) > checked:
        logger.warning(
            "Citation-integrity budget: %d of %d DOIs exceeded the Crossref "
            "retraction cap/time budget and stay unflagged.",
            len(batch_dois) - checked,
            len(batch_dois),
        )

    enriched: list[dict[str, Any]] = []
    for source in sources:
        enriched_source = dict(source)
        doi_key = _doi_of(source).lower()
        if doi_key and doi_key in papers:
            paper = papers[doi_key]
            if paper is None:
                # S2 answered and matched no paper — suspicious, not unknown.
                enriched_source["integrity_status"] = INTEGRITY_STATUS_UNRESOLVED
            else:
                enriched_source["integrity_status"] = INTEGRITY_STATUS_VERIFIED
                enriched_source["citation_count"] = paper.get("citation_count")
        else:
            # No DOI, over budget, or the S2 batch failed: honestly unknown.
            enriched_source["integrity_status"] = INTEGRITY_STATUS_UNKNOWN
        if retracted_by_doi.get(doi_key):
            enriched_source["integrity_status"] = INTEGRITY_STATUS_RETRACTED
            enriched_source["retracted"] = True
        enriched.append(enriched_source)
    return enriched
