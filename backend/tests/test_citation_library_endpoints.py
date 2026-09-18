"""
Citation library endpoint tests (R6): auth guards, import + dedupe, list.

The library is cross-report by design, so its endpoints sit behind the
API key only — no session token. The db layer is replaced with an
in-memory store mirroring db.py's first-seen-wins dedupe semantics.
"""

import uuid

import pytest
from citation_library import persistent_id_key
from conftest import API_KEY_HEADERS

RIS_FIXTURE = open("tests/fixtures/citation_library_import.ris").read()
BIBTEX_FIXTURE = open("tests/fixtures/golden_sample_report.bib").read()


@pytest.fixture
def library_store(monkeypatch):
    """In-memory citation library hooked into the server's db functions."""
    import server

    store: list[dict] = []

    def import_sources(records):
        imported = duplicates = 0
        for record in records:
            key = persistent_id_key(record)
            if key and any(row["persistent_id_key"] == key for row in store):
                duplicates += 1
                continue
            store.append(
                {
                    "id": str(uuid.uuid4()),
                    "persistent_id_key": key,
                    **record,
                }
            )
            imported += 1
        return {"imported": imported, "duplicates": duplicates}

    def list_sources(limit=200, offset=0, query=""):
        rows = [dict(row) for row in store]
        if query:
            folded = query.casefold()
            rows = [
                row
                for row in rows
                if folded in (row.get("title") or "").casefold()
            ]
        return rows[offset : offset + limit]

    monkeypatch.setattr(server, "library_import_sources", import_sources)
    monkeypatch.setattr(server, "library_list_sources", list_sources)
    return store


def test_library_endpoints_require_api_key(client, library_store):
    assert (
        client.get("/api/library/sources").status_code == 401
    )
    assert (
        client.post(
            "/api/library/import", json={"format": "ris", "content": "TY  - JOUR\nTI  - T\nER  -\n"}
        ).status_code
        == 401
    )


def test_library_import_ris_happy_path(client, library_store):
    response = client.post(
        "/api/library/import",
        json={"format": "ris", "content": RIS_FIXTURE},
        headers=API_KEY_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"imported": 4, "duplicates": 0, "parsed": 4}
    assert len(library_store) == 4
    # Every imported record renders the unknown-integrity degradation.
    assert all(row["integrity_status"] == "unknown" for row in library_store)


def test_library_import_bibtex_dedupes_on_reimport(client, library_store):
    first = client.post(
        "/api/library/import",
        json={"format": "bibtex", "content": BIBTEX_FIXTURE},
        headers=API_KEY_HEADERS,
    )
    assert first.json() == {"imported": 2, "duplicates": 0, "parsed": 2}

    second = client.post(
        "/api/library/import",
        json={"format": "bibtex", "content": BIBTEX_FIXTURE},
        headers=API_KEY_HEADERS,
    )
    # First-seen wins: the re-import bumps nothing user-visible, it only
    # counts duplicates — the library never grows identical records.
    assert second.json() == {"imported": 0, "duplicates": 2, "parsed": 2}
    assert len(library_store) == 2


def test_library_import_cross_format_dedupe(client, library_store):
    # The Vaswani work appears in both fixtures under DIFFERENT persistent
    # ids (RIS carries the DOI, BibTeX the arXiv ID) — different keys, both
    # kept: dedupe never fuzzy-matches titles. The example blog entry
    # shares one URL across formats, so it collapses to one record.
    client.post(
        "/api/library/import",
        json={"format": "ris", "content": RIS_FIXTURE},
        headers=API_KEY_HEADERS,
    )
    response = client.post(
        "/api/library/import",
        json={"format": "bibtex", "content": BIBTEX_FIXTURE},
        headers=API_KEY_HEADERS,
    )

    body = response.json()
    assert body["imported"] == 1
    assert body["duplicates"] == 1


def test_library_import_rejects_unknown_format(client, library_store):
    response = client.post(
        "/api/library/import",
        json={"format": "endnote", "content": "whatever"},
        headers=API_KEY_HEADERS,
    )
    assert response.status_code == 422


def test_library_import_no_records_422(client, library_store):
    response = client.post(
        "/api/library/import",
        json={"format": "ris", "content": "not a bibliography"},
        headers=API_KEY_HEADERS,
    )
    assert response.status_code == 422
    assert "No importable records" in response.json()["detail"]


def test_library_list_returns_imported_sources(client, library_store):
    client.post(
        "/api/library/import",
        json={"format": "ris", "content": RIS_FIXTURE},
        headers=API_KEY_HEADERS,
    )

    listing = client.get("/api/library/sources", headers=API_KEY_HEADERS)

    assert listing.status_code == 200
    body = listing.json()
    assert body["count"] == 4
    titles = {source["title"] for source in body["sources"]}
    assert "Attention Is All You Need" in titles
    keys = {source["persistent_id_key"] for source in body["sources"]}
    assert "doi:10.5555/3295222.3295349" in keys


def test_library_list_title_filter(client, library_store):
    client.post(
        "/api/library/import",
        json={"format": "ris", "content": RIS_FIXTURE},
        headers=API_KEY_HEADERS,
    )

    filtered = client.get(
        "/api/library/sources", params={"q": "retrieval"}, headers=API_KEY_HEADERS
    )

    body = filtered.json()
    assert body["count"] == 1
    assert body["sources"][0]["title"].startswith(
        "Retrieval-Augmented Generation"
    )
