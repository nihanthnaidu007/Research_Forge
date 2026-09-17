"""Provenance-merge and graceful-degradation tests for scholarly retrieval.

Covers the merge_scholarly_and_web_results contract (labeling, dedupe,
ordering, cap) and collect_scholarly_results degradation (a failing source
never stops research; remaining sources still contribute).
"""


from scholarly import merge
from scholarly.clients import ScholarlyAPIError


def web_result(url: str, score: float = 0.8) -> dict:
    return {
        "url": url,
        "title": f"Web: {url}",
        "snippet": "web snippet",
        "source_domain": url.split("//")[-1],
        "relevance_score": score,
    }


def scholarly_result(url: str, score: float = 0.9, api: str = "arxiv") -> dict:
    return {
        "url": url,
        "title": f"Paper: {url}",
        "snippet": "paper abstract",
        "source_domain": url.split("//")[-1],
        "relevance_score": score,
        "source_type": "scholarly",
        "source_api": api,
        "authors": ["A. Author"],
        "year": 2024,
        "venue": "Nature",
        "persistent_ids": {"doi": "10.1/x"},
    }


# --- Provenance merge ---------------------------------------------------------


def test_web_results_get_provenance_labels():
    merged = merge.merge_scholarly_and_web_results([], [web_result("https://a.com")])

    assert len(merged) == 1
    result = merged[0]
    assert result["source_type"] == "web"
    assert result["source_api"] == "tavily"
    assert result["persistent_ids"] == {}
    assert result["authors"] == []


def test_scholarly_results_keep_their_labels():
    scholarly = [scholarly_result("https://arxiv.org/abs/1")]
    merged = merge.merge_scholarly_and_web_results(scholarly, [])

    assert merged[0]["source_type"] == "scholarly"
    assert merged[0]["source_api"] == "arxiv"
    assert merged[0]["persistent_ids"] == {"doi": "10.1/x"}


def test_merge_deduplicates_by_url_with_scholarly_priority():
    # Same URL from both worlds: the scholarly record must win.
    scholarly = [scholarly_result("https://example.com/paper")]
    web = [web_result("https://example.com/paper")]

    merged = merge.merge_scholarly_and_web_results(scholarly, web)

    assert len(merged) == 1
    assert merged[0]["source_type"] == "scholarly"


def test_merge_is_sorted_by_relevance_and_limited():
    scholarly = [
        scholarly_result(f"https://s.com/{i}", score=0.5 + 0.1 * i) for i in range(6)
    ]
    web = [web_result(f"https://w.com/{i}", score=0.5) for i in range(6)]

    merged = merge.merge_scholarly_and_web_results(scholarly, web, limit=8)

    assert len(merged) == 8
    scores = [r["relevance_score"] for r in merged]
    assert scores == sorted(scores, reverse=True)


def test_merge_does_not_mutate_input_lists():
    scholarly = [scholarly_result("https://s.com/1")]
    web = [web_result("https://w.com/1")]

    merge.merge_scholarly_and_web_results(scholarly, web)

    # Labeling works on copies — the caller's dicts are untouched.
    assert "source_api" not in web[0]
    assert "source_type" not in web[0]
    assert scholarly[0]["source_type"] == "scholarly"
    assert scholarly[0]["persistent_ids"] == {"doi": "10.1/x"}


# --- Graceful degradation ------------------------------------------------------


def _source_returns(results):
    return lambda query, max_results: results


def test_collect_returns_results_from_healthy_sources():
    overrides = {
        "arxiv": _source_returns([scholarly_result("https://arxiv.org/abs/1")]),
        "crossref": _source_returns(
            [scholarly_result("https://doi.org/2", api="crossref")]
        ),
    }

    results, counts = merge.collect_scholarly_results(
        ["query"], source_overrides=overrides
    )

    assert counts == {"arxiv": 1, "crossref": 1}
    assert len(results) == 2


def test_collect_degrades_gracefully_when_one_api_fails():
    def broken(query, max_results):
        raise ScholarlyAPIError("arxiv is down")

    overrides = {
        "arxiv": broken,
        "crossref": _source_returns(
            [scholarly_result("https://doi.org/2", api="crossref")]
        ),
    }

    results, counts = merge.collect_scholarly_results(
        ["query"], source_overrides=overrides
    )

    # arXiv's failure must not lose Crossref's results.
    assert counts == {"crossref": 1}
    assert len(results) == 1
    assert results[0]["source_api"] == "crossref"


def test_collect_degrades_when_every_api_fails():
    def broken(query, max_results):
        raise ScholarlyAPIError("all scholarly sources down")

    results, counts = merge.collect_scholarly_results(
        ["query"], source_overrides={"arxiv": broken, "crossref": broken}
    )

    assert results == []
    assert counts == {}


def test_collect_dedupes_repeated_results_across_queries():
    repeating = _source_returns([scholarly_result("https://arxiv.org/abs/1")])

    # Same paper returned for both queries must collapse to one entry.
    results, counts = merge.collect_scholarly_results(
        ["q1", "q2"], source_overrides={"arxiv": repeating}
    )

    assert counts == {"arxiv": 1}
    assert len(results) == 1


# --- Citation provenance -------------------------------------------------------


def test_citation_records_carry_provenance_and_persistent_ids():
    from graph.agents.citations import build_citation_list

    research_results = [
        scholarly_result("https://arxiv.org/abs/1"),
        web_result("https://example.com/blog"),
    ]
    sections = [
        {
            "section_id": "s1",
            "title": "T",
            "content": "Claim one [source: https://arxiv.org/abs/1] and "
            "claim two [source: https://example.com/blog].",
            "word_count": 20,
            "sources_used": [],
        }
    ]

    citations = build_citation_list(sections, research_results)

    assert [c["citation_number"] for c in citations] == [1, 2]
    scholarly_citation, web_citation = citations

    assert scholarly_citation["source_type"] == "scholarly"
    assert scholarly_citation["source_api"] == "arxiv"
    assert scholarly_citation["persistent_ids"] == {"doi": "10.1/x"}
    assert scholarly_citation["authors"] == ["A. Author"]
    assert scholarly_citation["year"] == 2024

    assert web_citation["source_type"] == "web"
    assert web_citation["persistent_ids"] == {}
