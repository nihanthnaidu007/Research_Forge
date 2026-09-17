"""
ResearchForge BibTeX Exporter
Generates a .bib bibliography from stored citation metadata.

Every entry derives from the citation record produced by the citations
agent — title, authors, year, venue, and persistent identifiers (DOI,
arXiv ID, S2 paper ID) captured at retrieval time. Nothing is invented:
fields the retrieval source did not return are simply omitted.

Output is deterministic given the same report state (no timestamps), so
golden-file tests can assert the exact text.
"""

import re

from export.markdown_exporter import extract_report_parts

# BibTeX special characters that must be escaped inside field values
# (URLs are excluded — they render inside \url{}).
_BIBTEX_SPECIALS = str.maketrans(
    {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
)


def _escape(value: str) -> str:
    return value.translate(_BIBTEX_SPECIALS)


def _ascii_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())[:24]


def _bibtex_author_field(authors: list[str]) -> str:
    """Format 'Given Family' name strings as BibTeX 'Family, Given' pairs."""
    formatted = []
    for author in authors:
        parts = author.strip().split()
        if len(parts) > 1:
            formatted.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        elif parts:
            formatted.append(parts[0])
    return " and ".join(formatted)


def citation_key(source: dict, position: int) -> str:
    """
    Build a stable BibTeX key: first-author surname + year + first title
    word (e.g. ``smith2024quantum``), falling back to the citation number.
    """
    authors = source.get("authors") or []
    surname = authors[0].strip().split()[-1] if authors else ""
    year = source.get("year")
    title = (source.get("title") or "").strip()
    title_word = re.sub(r"[^A-Za-z]", "", title.split()[0]) if title.split() else ""

    key = _ascii_slug(f"{surname}{year or ''}{title_word}")
    if not key:
        key = f"source{position + 1}"
    return key


def _entry_type(source: dict) -> str:
    persistent_ids = source.get("persistent_ids") or {}
    if persistent_ids.get("doi") and source.get("venue"):
        return "article"
    return "misc"


def _entry_fields(source: dict) -> list[tuple[str, str]]:
    """
    Build the field list for one entry, deriving only from what retrieval
    actually captured.
    """
    persistent_ids = source.get("persistent_ids") or {}
    fields: list[tuple[str, str]] = [
        ("title", _escape(source.get("title") or "Untitled"))
    ]

    author_field = _bibtex_author_field(source.get("authors") or [])
    if author_field:
        fields.append(("author", _escape(author_field)))

    year = source.get("year")
    if year:
        fields.append(("year", str(year)))

    arxiv_id = persistent_ids.get("arxiv_id")
    doi = persistent_ids.get("doi")
    venue = source.get("venue") or ""

    if arxiv_id:
        fields.extend(
            [
                ("eprint", arxiv_id),
                ("archivePrefix", "arXiv"),
                ("url", source.get("url") or f"https://arxiv.org/abs/{arxiv_id}"),
            ]
        )
    elif doi:
        if _entry_type(source) == "article":
            fields.append(("journal", _escape(venue)))
        fields.append(("doi", doi))
        if source.get("url"):
            fields.append(("url", source["url"]))

    if source.get("url") and not any(name == "url" for name, _ in fields):
        fields.append(("howpublished", rf"\url{{{source['url']}}}"))

    # Provenance note: the citation says where it actually came from.
    if source.get("source_api"):
        fields.append(("note", f"Source: {source['source_api']}"))

    return fields


def build_bibtex_report(state: dict) -> str:
    """Render the report's citation list as a BibTeX bibliography."""
    parts = extract_report_parts(state)

    lines: list[str] = []
    used_keys: set[str] = set()

    for position, source in enumerate(parts["sources"]):
        base_key = citation_key(source, position)
        key = base_key
        suffix_letter = "a"
        while key in used_keys:  # BibTeX disambiguates duplicate keys with letters
            key = f"{base_key}{suffix_letter}"
            suffix_letter = chr(ord(suffix_letter) + 1)
        used_keys.add(key)

        lines.append(f"@{_entry_type(source)}{{{key},")
        for name, value in _entry_fields(source):
            lines.append(f"  {name} = {{{value}}},")
        lines.append("}")
        lines.append("")

    if not lines:
        lines.append("% No cited sources in this report.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
