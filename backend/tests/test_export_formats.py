"""Tests for the Markdown and HTML export endpoints.

Both endpoints reuse the W0-protected export flow: API key plus per-session
ownership token, the same guard chain as /api/export-pdf.
"""

import pytest
from conftest import API_KEY_HEADERS, seed_session

from export.markdown_exporter import build_html_report, build_markdown_report

COMPLETE_STATE = {
    "topic": "Quantum computing advances",
    "depth": "quick",
    "written_sections": [
        {
            "section_id": "sec_1",
            "title": "Background",
            "content": "Background prose with <script>danger</script> [1] citation.",
            "word_count": 42,
            "sources_used": ["https://example.com/a"],
        },
        {
            "section_id": "sec_2",
            "title": "Findings",
            "content": "Findings prose.",
            "word_count": 38,
            "sources_used": [],
        },
    ],
    "sources": [
        {
            "url": "https://example.com/a",
            "title": "Example",
            "domain": "example.com",
            "citation_number": 1,
        }
    ],
    "confidence_scores": {},
    "overall_confidence": 0.9,
}


def _seed_complete(store, status="complete", state=None):
    return seed_session(
        store,
        token="tok",
        status=status,
        state=state if state is not None else COMPLETE_STATE,
    )


def _authed(client, path, sid, token="tok"):
    return client.post(
        path,
        params={"session_id": sid},
        headers={**API_KEY_HEADERS, "X-Session-Token": token},
    )


# --- Negative auth: both endpoints fail closed -------------------------------


def test_markdown_export_requires_api_key(client, fake_db):
    sid = _seed_complete(fake_db)
    response = client.post("/api/export-markdown", params={"session_id": sid})
    assert response.status_code == 401


def test_html_export_requires_api_key(client, fake_db):
    sid = _seed_complete(fake_db)
    response = client.post("/api/export-html", params={"session_id": sid})
    assert response.status_code == 401


def test_markdown_export_requires_session_token(client, fake_db):
    sid = _seed_complete(fake_db)
    response = client.post(
        "/api/export-markdown", params={"session_id": sid}, headers=API_KEY_HEADERS
    )
    assert response.status_code == 401


def test_html_export_requires_session_token(client, fake_db):
    sid = _seed_complete(fake_db)
    response = client.post(
        "/api/export-html", params={"session_id": sid}, headers=API_KEY_HEADERS
    )
    assert response.status_code == 401


def test_markdown_export_rejects_wrong_token(client, fake_db):
    sid = _seed_complete(fake_db)
    assert _authed(client, "/api/export-markdown", sid, token="wrong").status_code == 403


def test_html_export_rejects_wrong_token(client, fake_db):
    sid = _seed_complete(fake_db)
    assert _authed(client, "/api/export-html", sid, token="wrong").status_code == 403


# --- State guards ------------------------------------------------------------


def test_markdown_export_unknown_session_404(client, fake_db):
    response = _authed(client, "/api/export-markdown", "00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_html_export_unknown_session_404(client, fake_db):
    response = _authed(client, "/api/export-html", "00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_markdown_export_rejects_incomplete_report(client, fake_db):
    sid = seed_session(
        fake_db,
        token="tok",
        status="waiting_approval",
        state={"outline": [], "stream_updates": []},
    )
    assert _authed(client, "/api/export-markdown", sid).status_code == 400


def test_markdown_export_rejects_state_without_sections(client, fake_db):
    sid = seed_session(
        fake_db, token="tok", status="complete", state={"stream_updates": []}
    )
    assert _authed(client, "/api/export-markdown", sid).status_code == 400


def test_html_export_rejects_state_without_sections(client, fake_db):
    sid = seed_session(
        fake_db, token="tok", status="complete", state={"stream_updates": []}
    )
    assert _authed(client, "/api/export-html", sid).status_code == 400


# --- Happy paths -------------------------------------------------------------


def test_markdown_export_happy_path(client, fake_db):
    sid = _seed_complete(fake_db)
    response = _authed(client, "/api/export-markdown", sid)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert disposition.startswith("attachment; filename=\"researchforge-")
    assert ".md\"" in disposition

    body = response.text
    assert "# Quantum computing advances" in body
    assert "## 1. Background" in body
    assert "## 2. Findings" in body
    assert "## References (1)" in body
    assert "example.com" in body


def test_html_export_happy_path_escapes_model_content(client, fake_db):
    sid = _seed_complete(fake_db)
    response = _authed(client, "/api/export-html", sid)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert ".html\"" in disposition

    body = response.text
    assert "<h1>Quantum computing advances</h1>" in body
    # Model-generated content must never land in the document unescaped.
    assert "<script>danger</script>" not in body
    assert "&lt;script&gt;danger&lt;/script&gt;" in body
    assert "<h2>1. Background</h2>" in body


# --- Exporter unit behavior --------------------------------------------------


def test_markdown_report_raises_on_empty_state():
    with pytest.raises(ValueError):
        build_markdown_report({"topic": "x", "written_sections": []})


def test_html_report_raises_on_empty_state():
    with pytest.raises(ValueError):
        build_html_report({"topic": "x", "written_sections": []})


def test_markdown_report_uses_default_topic_when_blank():
    state = {
        "topic": "",
        "written_sections": [
            {"section_id": "s", "title": "T", "content": "Body text.", "word_count": 2}
        ],
    }
    assert build_markdown_report(state).startswith("# Research Report")
