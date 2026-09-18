"""
Citation library parser and dedupe-key tests (R6).

Parsers run offline against recorded fixtures (tests/fixtures/) — no
live network. The dedupe key is the W1 persistent-id priority chain:
DOI > arXiv ID > Semantic Scholar ID > URL > title+year.
"""

from pathlib import Path

import citation_library as cl

# Resolve against this file's directory — pytest may run from the repo root.
FIXTURES = Path(__file__).resolve().parent / "fixtures"
RIS_FIXTURE = (FIXTURES / "citation_library_import.ris").read_text()
BIBTEX_FIXTURE = (FIXTURES / "golden_sample_report.bib").read_text()


def test_parse_bibtex_golden_fixture():
    records = cl.parse_bibtex(BIBTEX_FIXTURE)

    assert len(records) == 2
    vaswani = records[0]
    assert vaswani["title"] == "Attention Is All You Need"
    assert vaswani["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert vaswani["year"] == 2017
    assert vaswani["arxiv_id"] == "1706.03762"
    # arXiv ID outranks the URL in the dedupe priority chain.
    assert cl.persistent_id_key(vaswani) == "arxiv:1706.03762"

    example = records[1]
    assert example["authors"] == []
    assert example["url"] == "https://example.com/blog"
    assert cl.persistent_id_key(example) == "url:example.com/blog"


def test_parse_ris_fixture():
    records = cl.parse_ris(RIS_FIXTURE)

    assert len(records) == 4
    first = records[0]
    assert first["title"] == "Attention Is All You Need"
    assert first["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert first["source_type"] == "journal article"
    assert cl.persistent_id_key(first) == "doi:10.5555/3295222.3295349"

    second = records[1]
    assert second["source_type"] == "conference paper"
    # A DOI supplied as a doi.org URL still dedupes on the bare DOI.
    assert cl.persistent_id_key(second) == "doi:10.5555/3495724.3496440"

    third = records[2]
    assert third["source_type"] == "web page"
    assert cl.persistent_id_key(third) == "url:example.com/blog"


def test_parse_ris_folds_continuation_lines():
    record = cl.parse_ris(
        "TY  - JOUR\nTI  - A Long Title That\n   Wraps Across Lines\nER  -\n"
    )[0]
    assert record["title"] == "A Long Title That Wraps Across Lines"


def test_dedupe_key_priority_and_normalization():
    # DOI beats arXiv beats URL.
    record = {
        "title": "T",
        "doi": "https://doi.org/10.5555/ABC",
        "arxiv_id": "1706.03762",
        "url": "https://example.com/a",
    }
    assert cl.persistent_id_key(record) == "doi:10.5555/abc"

    # arXiv version suffixes are stripped so v1 and v2 collapse together.
    assert cl.persistent_id_key(
        {"title": "T", "arxiv_id": "arXiv:1706.03762v2"}
    ) == "arxiv:1706.03762"

    # Title+year fallback is whitespace-collapsed and case-folded.
    assert cl.persistent_id_key(
        {"title": "  Sparse   Attention  ", "year": 2024}
    ) == "title:sparse attention:2024"


def test_unidentifiable_record_has_no_dedupe_key():
    assert cl.persistent_id_key({"authors": ["A. Person"]}) is None


def test_imported_records_always_unknown_integrity():
    record = cl.normalize_imported_record(
        {"title": "T", "integrity_status": "verified"}
    )
    # Imports were not retrieved by the research pipeline — integrity is
    # rendered as unknown, never carried over or fabricated.
    assert record["integrity_status"] == "unknown"
