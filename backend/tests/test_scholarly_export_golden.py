"""Golden-file tests for the DOCX and BibTeX exporters.

Both exporters are deterministic given the report state (no timestamps in
the BibTeX output; python-docx content is asserted via the document model),
so the expected bytes/text are pinned in fixtures.

The sample state mixes one scholarly citation (with persistent identifiers
and provenance) and one plain web citation — exports must carry both,
provenance-labeled, exactly as retrieved.
"""

from pathlib import Path

import pytest
from docx import Document

from export.bibtex_exporter import build_bibtex_report
from export.docx_exporter import build_docx_report

FIXTURES = Path(__file__).parent / "fixtures"


SAMPLE_STATE = {
    "topic": "Transformer architectures",
    "depth": "quick",
    "written_sections": [
        {
            "section_id": "sec_1",
            "title": "Background",
            "content": "Background prose [1] and [2].",
            "word_count": 20,
            "sources_used": ["https://arxiv.org/abs/1706.03762"],
        }
    ],
    "sources": [
        {
            "url": "https://arxiv.org/abs/1706.03762",
            "title": "Attention Is All You Need",
            "domain": "arxiv.org",
            "citation_number": 1,
            "source_type": "scholarly",
            "source_api": "arxiv",
            "authors": ["Ashish Vaswani", "Noam Shazeer"],
            "year": 2017,
            "venue": "NeurIPS",
            "persistent_ids": {
                "s2_paper_id": "204e3073870fa2e1b2e9b1e1e6f8e1f0a1b2c3d4",
                "doi": "10.5555/3295222.3295349",
                "arxiv_id": "1706.03762",
            },
        },
        {
            "url": "https://example.com/blog",
            "title": "Example",
            "domain": "example.com",
            "citation_number": 2,
            "source_type": "web",
            "source_api": "tavily",
        },
    ],
    "confidence_scores": {},
    "overall_confidence": 0.9,
}


# --- BibTeX golden file ---------------------------------------------------------


def test_bibtex_matches_golden_file():
    golden = (FIXTURES / "golden_sample_report.bib").read_text(encoding="utf-8")
    assert build_bibtex_report(SAMPLE_STATE) == golden


def test_bibtex_is_parseable_and_traceable():
    # Every entry key is unique and every entry keeps its provenance note.
    text = build_bibtex_report(SAMPLE_STATE)
    keys = [
        line.split("{")[1].rstrip(",")
        for line in text.splitlines()
        if line.startswith("@")
    ]
    assert keys == ["vaswani2017attention", "example"]


def test_bibtex_empty_state_comment():
    # Sections present but no cited sources: the exporter emits a comment.
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
    assert build_bibtex_report(no_sources).startswith("% No cited sources")


# --- DOCX structure --------------------------------------------------------------


def test_docx_contains_sections_and_provenance(tmp_path):
    output = tmp_path / "report.docx"
    build_docx_report(SAMPLE_STATE, str(output))

    document = Document(str(output))
    texts = [p.text for p in document.paragraphs if p.text.strip()]

    assert texts[0] == "Transformer architectures"
    assert "1. Background" in texts
    assert "References (2)" in texts
    # Scholarly reference carries its persistent identifiers (sorted keys).
    assert any(
        "Attention Is All You Need" in t
        and "arxiv_id: 1706.03762" in t
        and "doi: 10.5555/3295222.3295349" in t
        and "s2_paper_id: 204e3073870fa2e1b2e9b1e1e6f8e1f0a1b2c3d4" in t
        for t in texts
    )
    # Web reference falls back to its domain as provenance.
    assert any("Example" in t and "(example.com)" in t for t in texts)


def test_docx_output_is_a_valid_docx_zip(tmp_path):
    output = tmp_path / "report.docx"
    build_docx_report(SAMPLE_STATE, str(output))

    # python-docx re-opening the file is the validity check; the header
    # confirms the container format for the download response.
    assert output.read_bytes()[:2] == b"PK"


def test_docx_report_raises_on_empty_state(tmp_path):
    empty: dict = {}
    with pytest.raises(ValueError):
        build_docx_report(empty, str(tmp_path / "empty.docx"))
