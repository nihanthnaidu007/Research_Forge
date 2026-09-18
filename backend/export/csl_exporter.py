"""CSL-style bibliography exporters: APA 7, MLA 9, and IEEE.

Deterministic, in-repo formatting over the captured Source metadata. No
citeproc-py or pandoc dependency: the persisted source fields (authors,
year, venue, persistent_ids) cover what these three fixed styles need,
and a CSL processor would add a heavyweight runtime for output a handful
of deterministic rules can produce. Only persisted fields are emitted —
missing authors, years, or identifiers degrade by omission ("n.d.",
URL-only entries), never fabricated.
"""

from __future__ import annotations

from typing import Any

CSL_STYLES = ("apa", "mla", "ieee")


def build_csl_bibliography(state: dict[str, Any], style: str) -> str:
    """Render the report's sources as a bibliography in the given style.

    APA and MLA sort alphabetically by first author (title when none);
    IEEE keeps citation order with [n] labels matching the report's
    in-text numbering. Raises ValueError for an unknown style or a
    report with no sources.
    """
    if style not in CSL_STYLES:
        expected = ", ".join(CSL_STYLES)
        raise ValueError(f"Unknown CSL style: {style!r} (expected one of {expected})")
    sources = state.get("sources") or []
    if not sources:
        raise ValueError("Report has no sources to export")

    if style == "ieee":
        ordered = sorted(sources, key=_ieee_sort_key)
        entries = [_format_ieee(src, index) for index, src in enumerate(ordered, start=1)]
    else:
        ordered = sorted(sources, key=_author_sort_key)
        formatter = _format_apa if style == "apa" else _format_mla
        entries = [formatter(src) for src in ordered]
    return "\n\n".join(entries) + "\n"


# --- shared field accessors -------------------------------------------------


def _clean_authors(source: dict[str, Any]) -> list[str]:
    return [a.strip() for a in (source.get("authors") or []) if a and a.strip()]


def _title(source: dict[str, Any]) -> str:
    return str(source.get("title") or "").strip() or "Untitled"


def _container(source: dict[str, Any]) -> str:
    """Venue for scholarly sources, site domain for plain web sources."""
    return str(source.get("venue") or "").strip() or str(source.get("domain") or "").strip()


def _doi(source: dict[str, Any]) -> str:
    ids = source.get("persistent_ids") or {}
    return str(ids.get("doi") or "").strip()


def _link(source: dict[str, Any]) -> str:
    """Canonical link: DOI resolver URL when we hold one, else the source URL."""
    doi = _doi(source)
    if doi:
        return f"https://doi.org/{doi}"
    return str(source.get("url") or "").strip()


def _year(source: dict[str, Any]) -> str:
    year = source.get("year")
    return str(year) if year else "n.d."


def _author_sort_key(source: dict[str, Any]) -> tuple[str, str]:
    authors = _clean_authors(source)
    surname = authors[0].split()[-1].lower() if authors else _title(source).lower()
    return (surname, _title(source).lower())


def _ieee_sort_key(source: dict[str, Any]) -> tuple[int, str]:
    number = source.get("citation_number")
    return (number if isinstance(number, int) else 10**6, _title(source).lower())


# --- author rendering -------------------------------------------------------


def _apa_name(name: str) -> str:
    """'Ashish Vaswani' -> 'Vaswani, A.' (APA reference-list initials)."""
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    surname = parts[-1]
    initials = " ".join(f"{part[0]}." for part in parts[:-1] if part)
    return f"{surname}, {initials}"


def _apa_authors(authors: list[str]) -> str:
    formatted = [_apa_name(a) for a in authors]
    if not formatted:
        return ""
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    if len(formatted) <= 20:
        return ", ".join(formatted[:-1]) + ", & " + formatted[-1]
    return ", ".join(formatted[:19]) + ", ... " + formatted[-1]


def _surname_first(name: str) -> str:
    """'Ashish Vaswani' -> 'Vaswani, Ashish' (MLA first-author form)."""
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[-1]}, {' '.join(parts[:-1])}"


def _mla_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return _surname_first(authors[0])
    if len(authors) == 2:
        return f"{_surname_first(authors[0])}, and {authors[1]}"
    return f"{_surname_first(authors[0])}, et al."


def _ieee_name(name: str) -> str:
    """'Ashish Vaswani' -> 'A. Vaswani' (IEEE initials-first form)."""
    parts = name.split()
    if len(parts) == 1:
        return parts[0]
    initials = " ".join(f"{part[0]}." for part in parts[:-1] if part)
    return f"{initials} {parts[-1]}"


def _ieee_authors(authors: list[str]) -> str:
    formatted = [_ieee_name(a) for a in authors]
    if not formatted:
        return ""
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]} and {formatted[1]}"
    return ", ".join(formatted[:-1]) + ", and " + formatted[-1]


# --- entry formatters -------------------------------------------------------


def _format_apa(source: dict[str, Any]) -> str:
    authors = _clean_authors(source)
    title = _title(source)
    container = _container(source)
    link = _link(source)

    if authors:
        parts: list[str] = [f"{_apa_authors(authors)} ({_year(source)}).", f"{title}."]
    else:
        # No author: the title moves into the author position (APA 9.12).
        parts = [f"{title}. ({_year(source)})."]
    if container:
        parts.append(f"{container}.")
    if link:
        parts.append(link)
    return " ".join(parts)


def _format_mla(source: dict[str, Any]) -> str:
    authors = _clean_authors(source)
    title = _title(source)
    container = _container(source)
    link = _link(source)

    parts = [f"{_mla_authors(authors)}."] if authors else []
    parts.append(f'"{title}."')
    if container:
        parts.append(f"{container},")
    year = source.get("year")
    if year:
        parts.append(f"{year},")
    if link:
        parts.append(f"{link}.")
    entry = " ".join(parts)
    # Normalize: an entry that never reached its closing period (no year,
    # no link) must not end on a dangling comma.
    return entry.rstrip(", ") if entry.endswith(",") else entry


def _format_ieee(source: dict[str, Any], index: int) -> str:
    label = source.get("citation_number") if isinstance(source.get("citation_number"), int) else index
    authors = _clean_authors(source)
    title = _title(source)
    container = _container(source)

    parts = [f"[{label}]"]
    if authors:
        parts.append(f"{_ieee_authors(authors)},")
    parts.append(f'"{title},"')
    year = source.get("year")
    if container:
        # A comma only when the year follows; otherwise the container
        # closes its own sentence before the doi/[Online] clause.
        parts.append(f"{container}," if year else f"{container}.")
    if year:
        parts.append(f"{year}.")
    doi = _doi(source)
    url = str(source.get("url") or "").strip()
    if doi:
        parts.append(f"doi: {doi}.")
    elif url:
        parts.append(f"[Online]. Available: {url}")
    return " ".join(parts)
