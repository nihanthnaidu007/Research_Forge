"""Unit tests for the scholarly API clients.

Every test runs against recorded API fixtures — no live network access.
requests.get is monkeypatched to return fixture payloads; time.sleep is
stubbed so retry tests are fast.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import requests
from scholarly import clients

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            # requests' raise_for_status always attaches the response.
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}", response=self
            )


class _StaticGetter:
    """Returns a canned response for every call, recording the last request."""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.last_url: str | None = None
        self.last_params: dict[str, Any] | None = None
        self.last_headers: dict[str, str] | None = None
        self.last_timeout: float | None = None

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.last_url = url
        self.last_params = params
        self.last_headers = headers
        self.last_timeout = timeout
        return self.response


@pytest.fixture(autouse=True)
def no_rate_limit_waits(monkeypatch):
    """Rate-limit spacing and retry backoff must not slow the suite."""
    monkeypatch.setattr(clients.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(clients, "_s2_limiter", clients.RateLimiter(0.0))
    monkeypatch.setattr(clients, "_arxiv_limiter", clients.RateLimiter(0.0))
    monkeypatch.setattr(clients, "_crossref_limiter", clients.RateLimiter(0.0))
    yield


# --- Semantic Scholar ---------------------------------------------------------


def test_s2_parses_fixture_and_captures_persistent_ids(monkeypatch):
    getter = _StaticGetter(
        FakeResponse(load_fixture("semantic_scholar_search.json"))
    )
    monkeypatch.setattr(clients, "requests", getter)
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    results = clients.search_semantic_scholar("attention is all you need")

    assert getter.last_url == clients.S2_SEARCH_URL
    assert getter.last_timeout == clients.REQUEST_TIMEOUT_SECONDS
    # Unauthenticated fallback: no x-api-key header when env is unset.
    assert "x-api-key" not in (getter.last_headers or {})

    # The paper without a paperId must be skipped, not cited untraceably.
    assert len(results) == 1
    result = results[0]
    assert result["source_type"] == "scholarly"
    assert result["source_api"] == "semantic_scholar"
    assert result["persistent_ids"] == {
        "s2_paper_id": "204e3073870fa2e1b2e9b1e1e6f8e1f0a1b2c3d4",
        "doi": "10.5555/3295222.3295349",
        "arxiv_id": "1706.03762",
    }
    assert result["title"] == "Attention Is All You Need"
    assert result["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert result["year"] == 2017
    assert result["venue"] == "NeurIPS"
    assert result["relevance_score"] == 1.0


def test_s2_optional_free_key_reaches_request_headers(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "free-tier-key")
    getter = _StaticGetter(
        FakeResponse(load_fixture("semantic_scholar_search.json"))
    )
    monkeypatch.setattr(clients, "requests", getter)

    results = clients.search_semantic_scholar("query")

    assert getter.last_headers is not None
    assert getter.last_headers["x-api-key"] == "free-tier-key"
    assert len(results) == 1


# --- arXiv --------------------------------------------------------------------


def test_arxiv_parses_fixture_and_extracts_ids(monkeypatch):
    getter = _StaticGetter(FakeResponse(load_fixture("arxiv_search.atom")))
    monkeypatch.setattr(clients, "requests", getter)

    results = clients.search_arxiv("graph neural networks")

    # The non-arXiv entry has no persistent id and must be skipped.
    assert len(results) == 1
    result = results[0]
    assert result["source_api"] == "arxiv"
    assert result["source_type"] == "scholarly"
    assert result["persistent_ids"] == {"arxiv_id": "2401.12345v2"}
    assert result["url"] == "https://arxiv.org/abs/2401.12345v2"
    assert result["title"] == "Graph Neural Networks: A Survey"
    assert result["authors"] == ["Zonghan Wu", "Shirui Pan"]
    assert result["year"] == 2024
    assert result["venue"] == "arXiv"


def test_arxiv_request_targets_atom_api(monkeypatch):
    getter = _StaticGetter(FakeResponse(load_fixture("arxiv_search.atom")))
    monkeypatch.setattr(clients, "requests", getter)

    clients.search_arxiv("query")

    assert getter.last_url == clients.ARXIV_API_URL


# --- Crossref -----------------------------------------------------------------


def test_crossref_parses_fixture_and_extracts_doi(monkeypatch):
    getter = _StaticGetter(FakeResponse(load_fixture("crossref_search.json")))
    monkeypatch.setattr(clients, "requests", getter)

    results = clients.search_crossref("protein structure prediction")

    assert len(results) == 2
    first = results[0]
    assert first["source_api"] == "crossref"
    assert first["source_type"] == "scholarly"
    assert first["persistent_ids"] == {"doi": "10.1038/s41586-021-03819-2"}
    assert (
        first["title"]
        == "Highly accurate protein structure prediction with AlphaFold"
    )
    assert first["authors"] == ["John Jumper", "Richard Evans"]
    assert first["year"] == 2021
    assert first["venue"] == "Nature"

    # Works without authors/venue still come through with their DOI.
    second = results[1]
    assert second["authors"] == []
    assert second["persistent_ids"] == {"doi": "10.1000/no-venue-doi"}


def test_crossref_polite_pool_params(monkeypatch):
    monkeypatch.setenv("CROSSREF_CONTACT_EMAIL", "lab@example.org")
    getter = _StaticGetter(FakeResponse(load_fixture("crossref_search.json")))
    monkeypatch.setattr(clients, "requests", getter)

    clients.search_crossref("query")

    assert getter.last_params is not None
    assert getter.last_params.get("mailto") == "lab@example.org"


def test_crossref_without_contact_email_has_no_mailto(monkeypatch):
    monkeypatch.delenv("CROSSREF_CONTACT_EMAIL", raising=False)
    getter = _StaticGetter(FakeResponse(load_fixture("crossref_search.json")))
    monkeypatch.setattr(clients, "requests", getter)

    clients.search_crossref("query")

    assert getter.last_params is not None
    assert "mailto" not in getter.last_params


# --- Retry / degradation behavior --------------------------------------------


def test_retry_on_5xx_then_success(monkeypatch):
    responses = [
        FakeResponse("server error", status_code=503),
        FakeResponse(load_fixture("crossref_search.json")),
    ]
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(clients.requests, "get", fake_get)

    results = clients.search_crossref("query")

    assert len(calls) == 2
    assert results[0]["persistent_ids"]["doi"] == "10.1038/s41586-021-03819-2"


def test_retry_exhaustion_raises_scholarly_api_error(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse("overloaded", status_code=503)

    monkeypatch.setattr(clients.requests, "get", fake_get)

    with pytest.raises(clients.ScholarlyAPIError):
        clients.search_crossref("query")


def test_non_transient_4xx_fails_immediately(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        return FakeResponse("bad request", status_code=400)

    monkeypatch.setattr(clients.requests, "get", fake_get)

    with pytest.raises(clients.ScholarlyAPIError):
        clients.search_semantic_scholar("query")

    assert len(calls) == 1  # no retry for a non-transient client error


def test_timeout_is_passed_to_every_request(monkeypatch):
    getter = _StaticGetter(FakeResponse(load_fixture("crossref_search.json")))
    monkeypatch.setattr(clients, "requests", getter)

    clients.search_crossref("query")

    assert getter.last_timeout == clients.REQUEST_TIMEOUT_SECONDS


def test_arxiv_fixture_is_valid_xml():
    # Guards against fixture corruption: the parser path must stay parseable.
    ET.fromstring(load_fixture("arxiv_search.atom"))
