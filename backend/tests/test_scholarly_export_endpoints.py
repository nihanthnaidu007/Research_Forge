"""Auth and happy-path tests for the /export-docx, /export-bibtex, and
/export-latex endpoints.

All endpoints reuse the W0-protected export flow: API key plus per-session
ownership token, the same guard chain as /api/export-md and /api/export-html.
"""

import pytest
from conftest import API_KEY_HEADERS, seed_session
from tests.test_scholarly_export_golden import SAMPLE_STATE

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _seed_complete(store, status="complete", state=None):
    return seed_session(
        store,
        token="tok",
        status=status,
        state=state if state is not None else SAMPLE_STATE,
    )


def _authed(client, path, sid, token="tok"):
    return client.post(
        path,
        params={"session_id": sid},
        headers={**API_KEY_HEADERS, "X-Session-Token": token},
    )


# --- Auth guards (all endpoints) -----------------------------------------------


@pytest.mark.parametrize("path", ["/api/export-docx", "/api/export-bibtex", "/api/export-latex"])
def test_export_requires_api_key(client, fake_db, path):
    response = client.post(path, params={"session_id": "s1"})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/api/export-docx", "/api/export-bibtex", "/api/export-latex"])
def test_export_requires_session_token(client, fake_db, path):
    sid = _seed_complete(fake_db)
    response = client.post(
        path, params={"session_id": sid}, headers=API_KEY_HEADERS
    )
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/api/export-docx", "/api/export-bibtex", "/api/export-latex"])
def test_export_rejects_wrong_token(client, fake_db, path):
    sid = _seed_complete(fake_db)
    response = _authed(client, path, sid, token="wrong-token")
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/api/export-docx", "/api/export-bibtex", "/api/export-latex"])
def test_export_unknown_session_404(client, fake_db, path):
    seed_session(fake_db, token="tok", status="complete", state=SAMPLE_STATE)
    response = _authed(client, path, "missing-session")
    assert response.status_code == 404


@pytest.mark.parametrize("path", ["/api/export-docx", "/api/export-bibtex", "/api/export-latex"])
def test_export_rejects_incomplete_report(client, fake_db, path):
    waiting = dict(SAMPLE_STATE, written_sections=[])
    sid = _seed_complete(fake_db, status="waiting_approval", state=waiting)
    response = _authed(client, path, sid)
    assert response.status_code in (400, 409)


# --- Happy paths -----------------------------------------------------------------


def test_latex_export_happy_path(client, fake_db):
    sid = _seed_complete(fake_db)

    response = _authed(client, "/api/export-latex", sid)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-latex")
    assert "attachment" in response.headers["content-disposition"]
    assert ".tex" in response.headers["content-disposition"]
    # Body is the self-contained document for the seeded state.
    assert response.text.startswith("\\documentclass")
    assert "\\begin{thebibliography}" in response.text
    assert response.text.rstrip().endswith("\\end{document}")


def test_bibtex_export_happy_path(client, fake_db):
    sid = _seed_complete(fake_db)

    response = _authed(client, "/api/export-bibtex", sid)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-bibtex")
    assert "attachment" in response.headers["content-disposition"]
    assert ".bib" in response.headers["content-disposition"]
    # Body is the deterministic bibliography for the seeded state.
    assert "@article{vaswani2017attention," in response.text
    assert "@misc{example," in response.text


def test_docx_export_happy_path(client, fake_db):
    sid = _seed_complete(fake_db)

    response = _authed(client, "/api/export-docx", sid)

    assert response.status_code == 200
    assert response.headers["content-type"] == DOCX_MEDIA_TYPE
    assert "attachment" in response.headers["content-disposition"]
    assert ".docx" in response.headers["content-disposition"]
    # Body is a real DOCX container (ZIP magic) with content.
    assert response.content[:2] == b"PK"
    assert len(response.content) > 0
