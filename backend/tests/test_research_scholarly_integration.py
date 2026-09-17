"""Agent integration test: scholarly sources enter the research loop.

The research node must merge provenance-labeled scholarly results with
Tavily web results and degrade gracefully when scholarly APIs fail —
Tavily alone must never be a single point of failure.
"""

import pytest

from graph.agents import research as research_module
from graph.agents.research import research_node


def web_result(url: str, score: float = 0.8) -> dict:
    return {
        "url": url,
        "title": f"Web: {url}",
        "snippet": "web snippet",
        "source_domain": url.split("//")[-1],
        "relevance_score": score,
    }


def scholarly_result(url: str, api: str = "arxiv") -> dict:
    return {
        "url": url,
        "title": f"Paper: {url}",
        "snippet": "paper abstract",
        "source_domain": url.split("//")[-1],
        "relevance_score": 0.95,
        "source_type": "scholarly",
        "source_api": api,
        "authors": ["A. Author"],
        "year": 2024,
        "venue": "Nature",
        "persistent_ids": {"doi": "10.1/x"},
    }


@pytest.fixture
def state():
    return {
        "topic": "protein folding",
        "depth": "quick",
        "stream_updates": [],
        "research_results": [],
        "retry_count": 0,
        "completed_agents": [],
        "error": None,
    }


def test_scholarly_sources_enter_the_research_loop(state, monkeypatch):
    """The provenance-labeled merge output lands in state.research_results."""
    scholarly_by_source = {
        "semantic_scholar": [
            scholarly_result("https://s2.com/1", "semantic_scholar")
        ],
        "arxiv": [scholarly_result("https://arxiv.org/abs/2")],
        "crossref": [scholarly_result("https://doi.org/3", "crossref")],
    }

    def fake_collect(queries):
        flattened = [r for rs in scholarly_by_source.values() for r in rs]
        counts = {k: len(v) for k, v in scholarly_by_source.items()}
        return flattened, counts

    monkeypatch.setattr(
        research_module, "collect_scholarly_results", fake_collect
    )
    monkeypatch.setattr(
        research_module,
        "perform_tavily_search",
        lambda query, **kwargs: [web_result("https://web.com/a")],
    )

    result = research_node(state)

    assert result["error"] is None
    urls = {r["url"] for r in result["research_results"]}
    assert urls == {
        "https://s2.com/1",
        "https://arxiv.org/abs/2",
        "https://doi.org/3",
        "https://web.com/a",
    }
    # Every merged result carries a provenance label — no anonymous sources.
    for entry in result["research_results"]:
        assert entry["source_type"] in ("web", "scholarly")
        assert entry["source_api"]
    assert "research" in result["completed_agents"]
    # The stream records which scholarly sources answered.
    assert any("Scholarly sources" in u for u in result["stream_updates"])


def test_research_degrades_to_web_when_all_scholarly_apis_fail(state, monkeypatch):
    monkeypatch.setattr(
        research_module,
        "collect_scholarly_results",
        lambda queries: ([], {}),
    )
    monkeypatch.setattr(
        research_module,
        "perform_tavily_search",
        lambda query, **kwargs: [web_result("https://web.com/a")],
    )

    result = research_node(state)

    assert result["error"] is None
    assert [r["url"] for r in result["research_results"]] == ["https://web.com/a"]
    assert all(r["source_type"] == "web" for r in result["research_results"])
    assert any("No scholarly sources" in u for u in result["stream_updates"])


def test_research_still_fails_when_everything_is_down(state, monkeypatch):
    monkeypatch.setattr(
        research_module,
        "collect_scholarly_results",
        lambda queries: ([], {}),
    )
    monkeypatch.setattr(
        research_module, "perform_tavily_search", lambda query, **kwargs: []
    )

    result = research_node(state)

    assert result["error"] is not None
    assert "scholarly APIs" in result["error"]
    assert result["research_results"] == []
