"""
Citation library import parsing and dedupe keys (R6).

Parses Zotero-exported RIS and BibTeX bibliographies into the same Source
shape the research pipeline persists, and derives the W1 persistent-id
dedupe key (DOI > arXiv ID > Semantic Scholar ID > URL > title+year) so
the same work imported twice collapses to one library record.

Imported sources are not retrieved by the research pipeline, so they
always carry integrity_status "unknown" — the library renders integrity,
never fabricates it.
"""

import re
from typing import Any

# A parsed, import-ready source record. Values are honest: keys are absent
# when the entry did not carry them — never defaulted to placeholders.
SourceRecord = dict[str, Any]

_RIS_TAG_LINE = re.compile(r"^([A-Z][A-Z0-9])  - ?(.*)$")
_RIS_TYPE_MAP = {
    "JOUR": "journal article",
    "CONF": "conference paper",
    "CHAP": "book chapter",
    "RPRT": "report",
    "THES": "thesis",
    "EJOUR": "web page",
    "WEB": "web page",
}

_BIBTEX_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]*)\s*,", re.IGNORECASE)
_BIBTEX_BRACE_DEPTH_RUNAWAY = 50


def _split_bibtex_fields(body: str) -> dict[str, str]:
    """Split a BibTeX entry body into field -> raw value.

    Handles braced values (including nested braces and embedded commas)
    and quoted values. Bare numeric values are taken as-is.
    """
    fields: dict[str, str] = {}
    position = 0
    length = len(body)
    while position < length:
        equals = body.find("=", position)
        if equals == -1:
            break
        # Field name: the last identifier chunk before '='.
        name_match = re.search(r"(\w+)\s*$", body[position:equals])
        if not name_match:
            position = equals + 1
            continue
        name = name_match.group(1).lower()
        cursor = equals + 1
        while cursor < length and body[cursor] in " \t\r\n":
            cursor += 1
        if cursor >= length:
            break
        if body[cursor] == "{":
            depth = 0
            start = cursor
            steps = 0
            while cursor < length and steps < _BIBTEX_BRACE_DEPTH_RUNAWAY * length:
                if body[cursor] == "{":
                    depth += 1
                elif body[cursor] == "}":
                    depth -= 1
                    if depth == 0:
                        cursor += 1
                        break
                cursor += 1
                steps += 1
            raw = body[start + 1 : cursor - 1] if depth == 0 else body[start + 1 :]
            # {...} may wrap another command like \url{...} — unwrap it.
            url_match = re.fullmatch(r"\\url\{(.*)\}", raw.strip(), re.DOTALL)
            raw = url_match.group(1) if url_match else raw
        elif body[cursor] == '"':
            end = body.find('"', cursor + 1)
            if end == -1:
                break
            raw = body[cursor + 1 : end]
            cursor = end + 1
        else:
            end = body.find(",", cursor)
            if end == -1:
                end = length
            raw = body[cursor:end].strip()
            cursor = end
        fields[name] = raw.strip()
        position = cursor
    return fields


def _clean_bibtex_value(value: str) -> str:
    """Strip residual braces and TeX escapes from a BibTeX field value."""
    cleaned = value.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_")
    return re.sub(r"[{}]", "", cleaned).strip()


def _normalize_person(name: str) -> str:
    """Normalize a person name to display order.

    "Vaswani, Ashish" and "Ashish Vaswani" both become "Ashish Vaswani".
    Purely mechanical reordering — no name inference.
    """
    name = _clean_bibtex_value(name.strip())
    if "," in name:
        last, _, first = name.partition(",")
        first = first.strip()
        last = last.strip()
        return f"{first} {last}".strip() if first else last
    return name


def parse_ris(text: str) -> list[SourceRecord]:
    """Parse RIS records (TY/ER delimited) into SourceRecord dicts."""

    def _record_from(tags: dict[str, list[str]]) -> SourceRecord | None:
        if not tags:
            return None
        title = next((v for v in tags.get("TI", []) + tags.get("T1", [])), "")
        if not title:
            return None
        ris_type = (tags.get("TY", ["GEN"])[0] or "GEN").upper()
        year_tag = tags.get("PY", []) + tags.get("Y1", [])
        year_raw = year_tag[0][:4] if year_tag else ""
        doi = (tags.get("DO", [""])[0] or "").strip()
        url = next(
            (v for v in tags.get("UR", []) + tags.get("LK", []) if v.strip()), ""
        )
        record: SourceRecord = {
            "title": _clean_bibtex_value(title),
            "authors": [
                _normalize_person(a)
                for a in tags.get("AU", []) + tags.get("A1", [])
                if a.strip()
            ],
            "year": int(year_raw) if year_raw.isdigit() else None,
            "venue": next((v for v in tags.get("JO", []) + tags.get("JF", []) + tags.get("T2", []) if v.strip()), None),
            "url": url or None,
            "doi": doi or None,
            "arxiv_id": None,
            "s2_id": None,
            "source_type": _RIS_TYPE_MAP.get(ris_type, "document"),
        }
        return record

    # Records are delimited by ER lines; walk the tag stream collecting them.
    records = []
    current: dict[str, list[str]] = {}
    for line in text.splitlines():
        match = _RIS_TAG_LINE.match(line.strip()) if line.strip() else None
        if match and match.group(1) == "ER":
            record = _record_from(current)
            if record:
                records.append(record)
            current = {}
        elif match:
            current.setdefault(match.group(1), []).append(match.group(2).strip())
        elif line.startswith((" ", "\t")) and current:
            last_tag = next(reversed(current))
            current[last_tag][-1] = f"{current[last_tag][-1]} {line.strip()}".strip()
    return records


def parse_bibtex(text: str) -> list[SourceRecord]:
    """Parse BibTeX entries into SourceRecord dicts."""
    records: list[SourceRecord] = []
    for entry_match in _BIBTEX_ENTRY.finditer(text):
        entry_type = entry_match.group(1).lower()
        if entry_type in ("comment", "preamble", "string"):
            continue
        # Entry body runs to the matching close brace at depth 0.
        depth = 1
        cursor = entry_match.end()
        start = cursor
        while cursor < len(text) and depth > 0:
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
            cursor += 1
        body = text[start : cursor - 1 if depth == 0 else len(text)]

        fields = _split_bibtex_fields(body)
        title = _clean_bibtex_value(fields.get("title", ""))
        if not title:
            continue

        authors = [
            _normalize_person(part)
            for part in re.split(r"\s+and\s+", fields.get("author", ""))
            if part.strip()
        ]
        year_raw = _clean_bibtex_value(fields.get("year", ""))
        url = _clean_bibtex_value(fields.get("url", fields.get("howpublished", "")))
        doi = _clean_bibtex_value(fields.get("doi", ""))
        arxiv_id: str | None = _clean_bibtex_value(fields.get("eprint", ""))
        if fields.get("archiveprefix", "").lower() != "arxiv":
            arxiv_id = None

        venue = fields.get("journal") or fields.get("booktitle") or fields.get(
            "publisher"
        )
        type_map = {
            "article": "journal article",
            "inproceedings": "conference paper",
            "incollection": "book chapter",
            "phdthesis": "thesis",
            "mastersthesis": "thesis",
            "techreport": "report",
        }
        records.append(
            {
                "title": title,
                "authors": authors,
                "year": int(year_raw) if year_raw.isdigit() else None,
                "venue": _clean_bibtex_value(venue) if venue else None,
                "url": _clean_bibtex_value(url) or None,
                "doi": doi or None,
                "arxiv_id": arxiv_id or None,
                "s2_id": _clean_bibtex_value(fields.get("s2_id", "")) or None,
                "source_type": type_map.get(entry_type, "document"),
            }
        )
    return records


_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "doi:")


def persistent_id_key(record: SourceRecord) -> str | None:
    """Derive the W1 persistent-id dedupe key for a record.

    Priority: DOI > arXiv ID > Semantic Scholar ID > URL > title+year.
    Returns None when nothing identifiable exists — such records are
    imported but never deduped (an unidentifiable source cannot be
    proven identical to another).
    """
    doi = (record.get("doi") or "").strip().lower()
    for prefix in _DOI_PREFIXES:
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    if doi:
        return f"doi:{doi}"

    arxiv = re.sub(r"^arxiv:\s*", "", (record.get("arxiv_id") or "").strip(), flags=re.IGNORECASE)
    arxiv = re.sub(r"v\d+$", "", arxiv)
    if arxiv:
        return f"arxiv:{arxiv.lower()}"

    s2 = (record.get("s2_id") or "").strip()
    if s2:
        return f"s2:{s2.lower()}"

    url = (record.get("url") or "").strip().lower()
    if url:
        url = re.sub(r"^https?://", "", url)
        url = url.rstrip("/")
        if url:
            return f"url:{url}"

    title = re.sub(r"\s+", " ", (record.get("title") or "").strip().lower())
    if title:
        year = record.get("year")
        suffix = f":{year}" if year else ""
        return f"title:{title}{suffix}"
    return None


def normalize_imported_record(record: SourceRecord) -> SourceRecord:
    """Stamp the import honesty invariants onto a parsed record.

    Imported sources were not retrieved by the research pipeline, so they
    always carry integrity_status "unknown" — the same degradation the UI
    already renders for unverified sources.
    """
    normalized = dict(record)
    normalized["integrity_status"] = "unknown"
    return normalized
