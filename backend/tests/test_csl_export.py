"""CSL bibliography export tests (R5): APA 7, MLA 9, IEEE.

Golden strings pin the deterministic formatting against the shared
scholarly+web fixture; degradation cases prove the exporter renders only
persisted metadata (n.d., URL-only entries) and never invents fields.
"""

import pytest
from tests.test_scholarly_export_golden import SAMPLE_STATE

from export.csl_exporter import build_csl_bibliography

SCHOLARLY = SAMPLE_STATE["sources"][0]
WEB = SAMPLE_STATE["sources"][1]


def test_apa_golden_strings():
    bibliography = build_csl_bibliography(SAMPLE_STATE, "apa")
    # Alphabetical: the authorless web entry sorts by title before Vaswani.
    assert bibliography == (
        "Example. (n.d.). example.com. https://example.com/blog\n\n"
        "Vaswani, A., & Shazeer, N. (2017). Attention Is All You Need. NeurIPS. "
        "https://doi.org/10.5555/3295222.3295349\n"
    )


def test_mla_golden_strings():
    bibliography = build_csl_bibliography(SAMPLE_STATE, "mla")
    assert bibliography == (
        '"Example." example.com, https://example.com/blog.\n\n'
        'Vaswani, Ashish, and Noam Shazeer. "Attention Is All You Need." '
        "NeurIPS, 2017, https://doi.org/10.5555/3295222.3295349.\n"
    )


def test_ieee_golden_strings_keep_citation_order():
    bibliography = build_csl_bibliography(SAMPLE_STATE, "ieee")
    assert bibliography == (
        '[1] A. Vaswani and N. Shazeer, "Attention Is All You Need," NeurIPS, 2017. '
        "doi: 10.5555/3295222.3295349.\n\n"
        '[2] "Example," example.com. [Online]. Available: https://example.com/blog\n'
    )


def test_apa_and_mla_sort_alphabetically_by_first_author():
    state = {
        "sources": [
            {**WEB, "citation_number": 1},
            {**SCHOLARLY, "citation_number": 2},
        ]
    }
    apa = build_csl_bibliography(state, "apa")
    # Example (no author, title sort) precedes Vaswani.
    assert apa.index("Example.") < apa.index("Vaswani")
    mla = build_csl_bibliography(state, "mla")
    assert mla.index('"Example."') < mla.index("Vaswani")


def test_ieee_labels_follow_citation_numbers_not_input_order():
    state = {
        "sources": [
            {**WEB, "citation_number": 2},
            {**SCHOLARLY, "citation_number": 1},
        ]
    }
    bibliography = build_csl_bibliography(state, "ieee")
    entries = bibliography.strip().split("\n\n")
    assert entries[0].startswith("[1] A. Vaswani")
    assert entries[1].startswith('[2] "Example')


def test_apa_many_authors_use_ellipsis_form():
    source = {
        **SCHOLARLY,
        "authors": [f"Author {i} Number" for i in range(1, 21)] + ["Zoe Lastname"],
    }
    bibliography = build_csl_bibliography({"sources": [source]}, "apa")
    entry = bibliography.strip()
    assert entry.startswith("Number, A.")
    assert "... Lastname, Z." in entry  # first 19 names, ellipsis, last author
    assert " & " not in entry


def test_mla_three_authors_collapse_to_et_al():
    source = {
        **SCHOLARLY,
        "authors": ["Ashish Vaswani", "Noam Shazeer", "Jakob Uszkoreit"],
    }
    bibliography = build_csl_bibliography({"sources": [source]}, "mla")
    assert bibliography.startswith("Vaswani, Ashish, et al.")


def test_missing_metadata_degrades_without_fabrication():
    bare_web = {
        "url": "https://example.org/page",
        "title": "Bare Page",
        "domain": "example.org",
        "citation_number": 1,
    }
    bibliography = build_csl_bibliography({"sources": [bare_web]}, "apa")
    assert bibliography == "Bare Page. (n.d.). example.org. https://example.org/page\n"

    ieee = build_csl_bibliography({"sources": [bare_web]}, "ieee")
    assert ieee == '[1] "Bare Page," example.org. [Online]. Available: https://example.org/page\n'


def test_unknown_style_raises():
    with pytest.raises(ValueError, match="Unknown CSL style"):
        build_csl_bibliography(SAMPLE_STATE, "chicago")


def test_empty_sources_raise():
    with pytest.raises(ValueError, match="no sources"):
        build_csl_bibliography({"sources": []}, "apa")
