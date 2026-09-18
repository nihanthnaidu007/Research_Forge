"""Golden-file and hostile-input tests for the LaTeX exporter.

The LaTeX exporter is deterministic given the report state (no timestamps),
so the expected text is pinned in a fixture — same discipline as the BibTeX
golden test. The hostile cases exist because the golden SAMPLE_STATE is
clean ASCII: model prose really contains LaTeX specials (a literal $ would
open math mode), typographic unicode, and citation markers the citations
agent left unresolvable — each gets its own pinning test.
"""

import re
from pathlib import Path

import pytest
from tests.test_scholarly_export_golden import SAMPLE_STATE

from export.latex_exporter import build_latex_report

FIXTURES = Path(__file__).parent / "fixtures"


def test_latex_matches_golden_file():
    golden = (FIXTURES / "golden_sample_report.tex").read_text(encoding="utf-8")
    assert build_latex_report(SAMPLE_STATE) == golden


def test_latex_is_self_contained_document():
    # Single-file contract: a full document with an embedded bibliography —
    # no .bib sidecar, nothing else to download.
    text = build_latex_report(SAMPLE_STATE)
    assert text.startswith(r"\documentclass")
    assert text.rstrip().endswith(r"\end{document}")
    assert r"\begin{thebibliography}{9}" in text
    assert r"\bibitem{vaswani2017attention}" in text
    assert ".bib" not in text


def test_latex_deterministic_no_timestamp():
    # Two renders of the same state are byte-identical (no generated-at).
    assert build_latex_report(SAMPLE_STATE) == build_latex_report(SAMPLE_STATE)


HOSTILE_STATE = {
    "topic": "Escaping & cost_2024",
    "depth": "deep",
    "written_sections": [
        {
            "section_id": "sec_1",
            "title": "Background & notes",
            "content": (
                "Cost is 100$ & rising: 50% #1 for R&D_2 {braces} ~x ^y "
                "\\alpha — “quoted” text… unresolved [7] leftover "
                "[source: https://example.com/x] resolvable [1]."
            ),
            "word_count": 40,
            "sources_used": [],
        }
    ],
    "sources": [
        {
            "url": "https://example.com/paper",
            "title": "Token_100% & Study",
            "domain": "example.com",
            "citation_number": 1,
            "source_api": "tavily",
        }
    ],
    "confidence_scores": {},
    "overall_confidence": 0.5,
}


def test_hostile_prose_cannot_open_math_mode():
    text = build_latex_report(HOSTILE_STATE)
    body = text.split(r"\begin{document}")[1]
    # Every raw special character in prose is escaped — the body must not
    # contain an unescaped $, %, or & outside \url{}.
    assert "100\\$" in body
    assert "50\\%" in body
    assert "\\#" in body
    assert "R\\&D\\_2" in body
    assert "\\{braces\\}" in body
    assert r"\textasciitilde{}x" in body
    assert r"\textasciicircum{}y" in body
    assert r"\textbackslash{}alpha" in body


def test_hostile_metadata_escaped():
    text = build_latex_report(HOSTILE_STATE)
    # Topic and titles with specials never break the preamble or sectioning.
    assert r"{\LARGE\bfseries Escaping \& cost\_2024}" in text
    assert r"\section*{1. Background \& notes}" in text
    assert r"\bibitem{token}" in text


def test_hostile_unicode_typographic_map():
    text = build_latex_report(HOSTILE_STATE)
    # Typographic characters become their LaTeX forms, not raw unicode.
    assert "---" in text
    assert "``quoted''" in text
    assert r"\ldots{}" in text
    assert "\u2014" not in text.split(r"\begin{document}")[1]


def test_unresolved_and_leftover_markers_stay_literal():
    text = build_latex_report(HOSTILE_STATE)
    body = text.split(r"\begin{document}")[1]
    # [7] has no source 7; [source: url] passes the pipeline verbatim —
    # both stay as literal (safe) text, only resolvable [1] becomes \cite.
    assert "[7]" in body
    assert "[source: https://example.com/x]" in body
    assert r"\cite{token}" in body


def test_duplicate_citation_keys_disambiguated():
    dup_state = {
        "topic": "t",
        "written_sections": [
            {
                "section_id": "sec_1",
                "title": "T",
                "content": "Prose [1] and [2].",
                "word_count": 4,
                "sources_used": [],
            }
        ],
        "sources": [
            {
                "url": "https://example.com/a",
                "title": "Same Title",
                "domain": "example.com",
                "citation_number": 1,
            },
            {
                "url": "https://example.com/b",
                "title": "Same Title",
                "domain": "example.com",
                "citation_number": 2,
            },
        ],
        "confidence_scores": {},
        "overall_confidence": 0.0,
    }
    text = build_latex_report(dup_state)
    keys = [
        match.group(1)
        for line in text.splitlines()
        if (match := re.match(r"\\bibitem\{([^}]+)\}", line))
    ]
    assert keys[0] != keys[1]
    assert len(keys) == 2
    # Both prose markers resolve to their own (deduped) key.
    assert rf"\cite{{{keys[0]}}}" in text
    assert rf"\cite{{{keys[1]}}}" in text


def test_missing_citation_number_still_gets_bibitem():
    no_number = dict(
        SAMPLE_STATE,
        sources=[dict(SAMPLE_STATE["sources"][0], citation_number=None)],
    )
    text = build_latex_report(no_number)
    assert r"\bibitem{vaswani2017attention}" in text
    # Nothing in the prose can cite it by number: [1] stays literal.
    assert "Background prose [1] and [2]." in text
    assert r"\cite{" not in text


def test_empty_sources_emits_comment_bibliography():
    no_sources = {
        "topic": "t",
        "written_sections": [
            {
                "section_id": "sec_1",
                "title": "T",
                "content": "Prose.",
                "word_count": 2,
                "sources_used": [],
            }
        ],
        "sources": [],
        "confidence_scores": {},
        "overall_confidence": 0.0,
    }
    text = build_latex_report(no_sources)
    assert "% No cited sources in this report." in text


def test_urls_render_inside_url_macro():
    text = build_latex_report(SAMPLE_STATE)
    # URLs pass raw inside \url{} — no escaping of underscores/percent there.
    assert r"\url{https://arxiv.org/abs/1706.03762}" in text


def test_report_raises_on_empty_state():
    empty: dict = {}
    with pytest.raises(ValueError):
        build_latex_report(empty)
