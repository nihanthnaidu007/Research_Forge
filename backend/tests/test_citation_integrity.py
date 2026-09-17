"""Unit tests for citation-integrity enrichment (W2).

Every test runs against recorded API fixtures — no live network access.
requests.get / requests.post are monkeypatched to return fixture payloads;
rate-limit spacing and retry backoff are stubbed so the suite stays fast.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import requests as requests_module
from scholarly import clients

from graph.agents.citation_integrity import (
    enrich_citations_with_integrity,
    unique_dois_in_order,
)
from graph.state import Source

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests_module.exceptions.HTTPError(
                f"HTTP {self.status_code}", response=self
            )


class _RoutingGetter:
    """
    Canned responses routed by HTTP verb + URL, recording every request.

    `post_handlers` maps URL → response text; `get_handlers` maps a URL
    prefix → response text (first match wins), so Crossref work lookups can
    be scripted per DOI.
    """

    def __init__(
        self,
        post_handlers: dict[str, str] | None = None,
        get_handlers: dict[str, str] | None = None,
        default_status: int = 200,
    ):
        self.post_handlers = post_handlers or {}
        self.get_handlers = get_handlers or {}
        self.default_status = default_status
        self.calls: list[tuple[str, str]] = []  # (verb, url)
        self.last_post_body: Any = None
        self.last_post_params: dict[str, Any] | None = None
        self.last_post_headers: dict[str, str] | None = None

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("get", url))
        for prefix, text in self.get_handlers.items():
            if url.startswith(prefix):
                return FakeResponse(text, self.default_status)
        return FakeResponse("not found", 404)

    def post(self, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append(("post", url))
        self.last_post_body = json
        self.last_post_params = params
        self.last_post_headers = headers
        if url in self.post_handlers:
            return FakeResponse(self.post_handlers[url], self.default_status)
        return FakeResponse("not found", 404)


@pytest.fixture(autouse=True)
def no_rate_limit_waits(monkeypatch):
    """Rate-limit spacing and retry backoff must not slow the suite."""
    monkeypatch.setattr(clients.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(clients, "_s2_limiter", clients.RateLimiter(0.0))
    monkeypatch.setattr(clients, "_crossref_limiter", clients.RateLimiter(0.0))
    yield


def make_source(**overrides: Any) -> dict[str, Any]:
    source = {
        "url": "https://example.com/paper",
        "title": "A Paper",
        "domain": "example.com",
        "citation_number": 1,
        "source_type": "scholarly",
        "source_api": "semantic_scholar",
        "snippet": "Some claim from the paper.",
        "persistent_ids": {"doi": "10.5555/3295222.3295349", "s2_paper_id": "abc"},
        "integrity_status": "unknown",
        "retracted": False,
        "citation_count": None,
    }
    source.update(overrides)
    return source


def fake_s2_lookup(results: dict[str, dict[str, Any] | None]):
    def lookup(dois):
        return {doi: results.get(doi) for doi in dois}

    return lookup


def fake_crossref(retracted_dois: set[str]):
    calls = []

    def lookup(doi):
        calls.append(doi)
        return doi in retracted_dois

    lookup.calls = calls
    return lookup


# --- scholarly.clients.get_paper_integrity ------------------------------------


def test_s2_batch_posts_doi_prefixed_ids_and_parses_fixture(monkeypatch):
    getter = _RoutingGetter(
        post_handlers={clients.S2_BATCH_URL: load_fixture("s2_paper_batch.json")}
    )
    monkeypatch.setattr(clients, "requests", getter)
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    result = clients.get_paper_integrity(
        ["10.5555/3295222.3295349", "10.9999/bogus-does-not-exist"]
    )

    assert getter.calls == [("post", clients.S2_BATCH_URL)]
    # One batched request carrying DOI:-prefixed ids, in order.
    assert getter.last_post_body == {
        "ids": ["DOI:10.5555/3295222.3295349", "DOI:10.9999/bogus-does-not-exist"]
    }
    assert getter.last_post_params == {"fields": clients.S2_INTEGRITY_FIELDS}

    # Matched DOI → integrity payload; unmatched DOI → None (live-verified
    # S2 batch behavior: null entry for a DOI it cannot match).
    matched = result["10.5555/3295222.3295349"]
    assert matched is not None
    assert matched["s2_paper_id"] == "5c5751d45e298cea054f32b392c12c61027d2fe7"
    assert matched["citation_count"] == 453
    assert result["10.9999/bogus-does-not-exist"] is None


def test_s2_batch_optional_key_reaches_headers(monkeypatch):
    getter = _RoutingGetter(
        post_handlers={clients.S2_BATCH_URL: load_fixture("s2_paper_batch.json")}
    )
    monkeypatch.setattr(clients, "requests", getter)
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "free-tier-key")

    clients.get_paper_integrity(["10.5555/3295222.3295349"])

    assert getter.last_post_headers is not None
    assert getter.last_post_headers["x-api-key"] == "free-tier-key"


def test_s2_batch_empty_input_sends_no_request(monkeypatch):
    getter = _RoutingGetter()
    monkeypatch.setattr(clients, "requests", getter)

    assert clients.get_paper_integrity([]) == {}
    assert clients.get_paper_integrity(["", "   "]) == {}
    assert getter.calls == []


def test_s2_batch_failure_raises_for_caller_degradation(monkeypatch):
    def fake_post(url, params=None, json=None, headers=None, timeout=None):
        return FakeResponse("overloaded", status_code=503)

    monkeypatch.setattr(clients.requests, "post", fake_post)

    with pytest.raises(clients.ScholarlyAPIError):
        clients.get_paper_integrity(["10.5555/3295222.3295349"])


# --- scholarly.clients.get_crossref_integrity ---------------------------------


def test_crossref_retraction_detected_from_updated_by(monkeypatch):
    getter = _RoutingGetter(
        get_handlers={
            clients.CROSSREF_API_URL: load_fixture("crossref_work_retracted.json")
        }
    )
    monkeypatch.setattr(clients, "requests", getter)

    assert clients.get_crossref_integrity("10.1016/S0140-6736(97)11096-0") is True
    # Retraction Watch metadata rides the work record: /works/{doi}.
    assert getter.calls == [
        ("get", f"{clients.CROSSREF_API_URL}/10.1016/S0140-6736(97)11096-0")
    ]


def test_crossref_clean_work_is_not_retracted(monkeypatch):
    getter = _RoutingGetter(
        get_handlers={
            clients.CROSSREF_API_URL: load_fixture("crossref_work_clean.json")
        }
    )
    monkeypatch.setattr(clients, "requests", getter)

    assert clients.get_crossref_integrity("10.1145/3442188.3445922") is False


def test_crossref_correction_update_alone_is_not_a_retraction(monkeypatch):
    """A correction notice must not flag a citation retracted."""
    work = json.loads(load_fixture("crossref_work_clean.json"))
    work["message"]["updated-by"] = [
        {"DOI": "10.9999/correction", "type": "correction", "label": "Correction"}
    ]
    getter = _RoutingGetter(
        get_handlers={clients.CROSSREF_API_URL: json.dumps(work)}
    )
    monkeypatch.setattr(clients, "requests", getter)

    assert clients.get_crossref_integrity("10.1145/3442188.3445922") is False


def test_crossref_unknown_doi_404_degrades_to_not_retracted(monkeypatch):
    """Non-Crossref DOIs (e.g. DataCite arXiv) fail fast — no retraction."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return FakeResponse("not found", status_code=404)

    monkeypatch.setattr(clients.requests, "get", fake_get)

    assert clients.get_crossref_integrity("10.48550/arXiv.2401.12345") is False
    assert len(calls) == 1  # 404 is not transient — no retry


def test_crossref_outage_degrades_to_not_retracted(monkeypatch):
    """Exhausted retries keep the citation unflagged (never a guess)."""
    warnings = []

    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse("overloaded", status_code=503)

    monkeypatch.setattr(clients.requests, "get", fake_get)
    monkeypatch.setattr(
        clients.logger, "warning", lambda msg, *a: warnings.append(msg)
    )

    assert clients.get_crossref_integrity("10.1145/3442188.3445922") is False
    assert warnings  # degradation is logged, never swallowed silently


# --- graph.agents.citation_integrity: pure enrichment --------------------------


def test_verified_enrichment_attaches_citation_count():
    sources = [make_source()]
    s2 = fake_s2_lookup(
        {
            "10.5555/3295222.3295349": {
                "s2_paper_id": "abc",
                "citation_count": 453,
                "title": "A Paper",
                "year": 2018,
                "venue": "NeurIPS",
            }
        }
    )

    enriched = enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=s2,
        lookup_crossref_retraction=fake_crossref(set()),
    )

    assert enriched[0]["integrity_status"] == "verified"
    assert enriched[0]["citation_count"] == 453
    assert enriched[0]["retracted"] is False


def test_retraction_overrides_verified_status():
    sources = [make_source(citation_number=1)]

    enriched = enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=fake_s2_lookup(
            {"10.5555/3295222.3295349": {"s2_paper_id": "abc", "citation_count": 12}}
        ),
        lookup_crossref_retraction=fake_crossref({"10.5555/3295222.3295349"}),
    )

    assert enriched[0]["integrity_status"] == "retracted"
    assert enriched[0]["retracted"] is True
    assert enriched[0]["citation_count"] == 12  # S2 count still attached


def test_missing_doi_degrades_to_unknown_without_any_lookup():
    sources = [make_source(source_type="web", source_api="tavily", persistent_ids={})]
    s2 = fake_s2_lookup({"10.5555/3295222.3295349": None})
    crossref = fake_crossref(set())

    enriched = enrich_citations_with_integrity(
        sources, lookup_paper_integrity=s2, lookup_crossref_retraction=crossref
    )

    assert enriched[0]["integrity_status"] == "unknown"
    assert enriched[0]["citation_count"] is None
    assert crossref.calls == []


def test_s2_batch_failure_degrades_every_doi_source_to_unknown():
    sources = [make_source(), make_source(citation_number=2)]

    def failing_lookup(dois):
        raise clients.ScholarlyAPIError("S2 down after 3 attempts")

    enriched = enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=failing_lookup,
        lookup_crossref_retraction=fake_crossref(set()),
    )

    assert [s["integrity_status"] for s in enriched] == ["unknown", "unknown"]
    assert all(s["citation_count"] is None for s in enriched)


def test_s2_failure_still_applies_crossref_retraction_evidence():
    """Retraction is an independent signal: S2 down, Crossref confirms."""
    sources = [make_source()]

    enriched = enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=lambda dois: (_ for _ in ()).throw(
            clients.ScholarlyAPIError("S2 down")
        ),
        lookup_crossref_retraction=fake_crossref({"10.5555/3295222.3295349"}),
    )

    assert enriched[0]["integrity_status"] == "retracted"
    assert enriched[0]["retracted"] is True


def test_unmatched_doi_reports_unresolved_not_unknown():
    """S2 answered 'no such paper' — a fabrication signal, not a failure."""
    sources = [make_source()]

    enriched = enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=fake_s2_lookup({"10.5555/3295222.3295349": None}),
        lookup_crossref_retraction=fake_crossref(set()),
    )

    assert enriched[0]["integrity_status"] == "unresolved"
    assert enriched[0]["citation_count"] is None


def test_same_doi_across_citations_merges_enrichment_without_duplicates():
    """URL-vs-DOI dedup: one lookup per DOI, enrichment merged on match."""
    sources = [
        make_source(url="https://example.com/paper", citation_number=1),
        make_source(
            url="https://www.semanticscholar.org/paper/abc",
            domain="semanticscholar.org",
            citation_number=2,
        ),
    ]
    crossref = fake_crossref(set())

    enriched = enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=fake_s2_lookup(
            {"10.5555/3295222.3295349": {"s2_paper_id": "abc", "citation_count": 7}}
        ),
        lookup_crossref_retraction=crossref,
    )

    assert len(enriched) == 2  # never appends duplicate Source entries
    assert crossref.calls == ["10.5555/3295222.3295349"]  # one lookup per DOI
    assert all(s["integrity_status"] == "verified" for s in enriched)
    assert all(s["citation_count"] == 7 for s in enriched)


def test_case_insensitive_doi_dedup_keeps_one_lookup(monkeypatch):
    getter_calls = []

    def lookup(dois):
        getter_calls.extend(dois)
        return {doi: {"s2_paper_id": "abc", "citation_count": 3} for doi in dois}

    sources = [
        make_source(persistent_ids={"doi": "10.5555/ABC"}, citation_number=1),
        make_source(persistent_ids={"doi": "10.5555/abc"}, citation_number=2),
    ]

    enriched = enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=lookup,
        lookup_crossref_retraction=fake_crossref(set()),
    )

    assert getter_calls == ["10.5555/ABC"]
    assert all(s["integrity_status"] == "verified" for s in enriched)


def test_crossref_cap_bounds_retraction_lookups():
    sources = [
        make_source(persistent_ids={"doi": f"10.5555/{i}"}, citation_number=i + 1)
        for i in range(5)
    ]
    crossref = fake_crossref(set())

    enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=fake_s2_lookup({}),
        lookup_crossref_retraction=crossref,
        max_crossref_lookups=2,
    )

    assert len(crossref.calls) == 2  # first-appearance order wins the budget


def test_time_budget_bounds_retraction_lookups():
    sources = [
        make_source(persistent_ids={"doi": f"10.5555/{i}"}, citation_number=i + 1)
        for i in range(5)
    ]
    crossref = fake_crossref(set())
    ticks = iter([0.0, 0.1, 20.0, 20.1, 20.2, 20.3])  # budget exhausted at 3rd

    enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=fake_s2_lookup({}),
        lookup_crossref_retraction=crossref,
        time_budget_seconds=15.0,
        clock=lambda: next(ticks),
    )

    assert len(crossref.calls) == 1


def test_s2_batch_cap_marks_over_budget_dois_unknown():
    sources = [
        make_source(persistent_ids={"doi": "10.5555/1"}, citation_number=1),
        make_source(persistent_ids={"doi": "10.5555/2"}, citation_number=2),
    ]
    batched = []

    def lookup(dois):
        batched.extend(dois)
        return {doi: {"s2_paper_id": "x", "citation_count": 1} for doi in dois}

    enriched = enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=lookup,
        lookup_crossref_retraction=fake_crossref(set()),
        max_s2_dois=1,
    )

    assert batched == ["10.5555/1"]
    assert enriched[0]["integrity_status"] == "verified"
    assert enriched[1]["integrity_status"] == "unknown"


def test_enrichment_is_pure_input_not_mutated():
    sources = [make_source()]

    enrich_citations_with_integrity(
        sources,
        lookup_paper_integrity=fake_s2_lookup(
            {"10.5555/3295222.3295349": {"s2_paper_id": "abc", "citation_count": 9}}
        ),
        lookup_crossref_retraction=fake_crossref(set()),
    )

    assert sources[0]["integrity_status"] == "unknown"
    assert sources[0]["citation_count"] is None


def test_unique_dois_in_order_preserves_first_appearance():
    sources = [
        make_source(persistent_ids={"doi": "10.5555/b"}, citation_number=1),
        make_source(persistent_ids={}, citation_number=2),
        make_source(persistent_ids={"doi": "10.5555/a"}, citation_number=3),
        make_source(persistent_ids={"doi": "10.5555/b"}, citation_number=4),
    ]

    assert unique_dois_in_order(sources) == ["10.5555/b", "10.5555/a"]


# --- citations_node integration -------------------------------------------------


def _integration_state() -> dict[str, Any]:
    """A state shaped like a late-stage graph run with scholarly results."""
    return {
        "written_sections": [
            {
                "section_id": "sec_1",
                "title": "Background",
                "content": "Transformers changed NLP [source: https://arxiv.org/abs/1706.03762]. Web context [source: https://example.com/page].",
                "word_count": 20,
                "sources_used": [],
            }
        ],
        "research_results": [
            {
                "url": "https://arxiv.org/abs/1706.03762",
                "title": "Attention Is All You Need",
                "snippet": "The dominant sequence transduction models...",
                "source_domain": "arxiv.org",
                "source_type": "scholarly",
                "source_api": "arxiv",
                "persistent_ids": {"arxiv_id": "1706.03762"},
            },
            {
                "url": "https://example.com/page",
                "title": "A Web Page",
                "snippet": "Web snippet.",
                "source_domain": "example.com",
                "source_type": "web",
                "source_api": "tavily",
            },
        ],
        "confidence_scores": {"sec_1": 0.9},
        "stream_updates": [],
        "completed_agents": [],
        "error": None,
    }


def test_citations_node_enriches_integrity_via_recorded_fixtures(monkeypatch):
    """Full path against fixtures: citations_node → enrichment → S2/Crossref."""
    from graph.agents import citations as citations_module

    getter = _RoutingGetter(
        post_handlers={
            # arXiv results carry no DOI, so nothing reaches S2 in this state.
        },
        get_handlers={},
    )
    monkeypatch.setattr(clients, "requests", getter)

    state = _integration_state()
    result = citations_module.citations_node(state)

    assert result["is_complete"] is True
    assert result["error"] is None
    sources = result["sources"]
    assert len(sources) == 2
    # arXiv-only citation: no DOI → honestly unknown; integrity pass ran.
    assert sources[0]["integrity_status"] == "unknown"
    assert sources[1]["integrity_status"] == "unknown"
    assert sources[0]["snippet"] == "The dominant sequence transduction models..."
    assert "verified against S2/Crossref" in result["stream_updates"][-1]


def test_citations_node_routes_doi_sources_through_s2_batch(monkeypatch):
    from graph.agents import citations as citations_module

    batch_fixture = json.dumps(
        [
            {
                "paperId": "204e3073870fa2e1b2e9b1e1e6f8e1f0a1b2c3d4",
                "title": "Attention Is All You Need",
                "year": 2017,
                "venue": "NeurIPS",
                "citationCount": 120000,
            }
        ]
    )
    getter = _RoutingGetter(post_handlers={clients.S2_BATCH_URL: batch_fixture})
    monkeypatch.setattr(clients, "requests", getter)

    state = _integration_state()
    # Give the arXiv result a Crossref DOI so the batch path is exercised.
    state["research_results"][0]["persistent_ids"] = {
        "arxiv_id": "1706.03762",
        "doi": "10.5555/3295222.3295349",
    }
    result = citations_module.citations_node(state)

    # Exactly one batched S2 request (plus the per-DOI Crossref retraction GET).
    posts = [call for call in getter.calls if call[0] == "post"]
    assert posts == [("post", clients.S2_BATCH_URL)]
    sources = result["sources"]
    assert sources[0]["integrity_status"] == "verified"
    assert sources[0]["citation_count"] == 120000
    assert sources[1]["integrity_status"] == "unknown"


def test_citations_node_survives_total_integrity_outage(monkeypatch):
    """Research never fails because an integrity API is down."""
    from graph.agents import citations as citations_module

    def connection_error(*args, **kwargs):
        raise requests_module.exceptions.ConnectionError("network unreachable")

    # Real client functions run their retry logic against a dead network;
    # the enrichment pass must degrade every citation to "unknown".
    monkeypatch.setattr(clients.requests, "get", connection_error)
    monkeypatch.setattr(clients.requests, "post", connection_error)

    state = _integration_state()
    state["research_results"][0]["persistent_ids"] = {"doi": "10.5555/3295222.3295349"}
    result = citations_module.citations_node(state)

    assert result["is_complete"] is True
    assert result["error"] is None
    assert all(s["integrity_status"] == "unknown" for s in result["sources"])


def test_citations_node_checkpoint_compat_old_state_shape():
    """
    A pre-W2 checkpoint restores without schema errors: sources lack the
    integrity keys entirely, research results lack persistent_ids — the
    citation pass still completes with honest defaults.
    """
    from graph.agents import citations as citations_module

    old_state = _integration_state()
    old_state["sources"] = [
        {
            "url": "https://arxiv.org/abs/1706.03762",
            "title": "Attention Is All You Need",
            "domain": "arxiv.org",
            "citation_number": 1,
        }
    ]

    result = citations_module.citations_node(old_state)

    assert result["is_complete"] is True
    assert result["error"] is None
    rebuilt = result["sources"]
    assert rebuilt[0]["integrity_status"] == "unknown"
    assert rebuilt[0]["retracted"] is False
    assert rebuilt[0]["citation_count"] is None


def test_source_model_defaults_keep_old_serialized_sources_loadable():
    """Pydantic defaults fill the W2 fields on pre-W2 serialized sources."""
    source = Source(
        url="https://arxiv.org/abs/1706.03762",
        title="Attention Is All You Need",
        domain="arxiv.org",
        citation_number=1,
    )
    assert source.integrity_status == "unknown"
    assert source.retracted is False
    assert source.citation_count is None
    assert source.snippet == ""


def test_enriched_sources_validate_against_source_model():
    sources = enrich_citations_with_integrity(
        [make_source()],
        lookup_paper_integrity=fake_s2_lookup(
            {"10.5555/3295222.3295349": {"s2_paper_id": "abc", "citation_count": 453}}
        ),
        lookup_crossref_retraction=fake_crossref({"10.5555/3295222.3295349"}),
    )
    model = Source(**sources[0])
    assert model.integrity_status == "retracted"
    assert model.retracted is True
