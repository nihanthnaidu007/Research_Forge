"""Integration tests over the run → upload → approve → download lifecycle.

The OpenAI/Tavily-backed graph pipeline is replaced by a scripted FakeGraph
(external clients raise if invoked — see conftest); everything else runs for
real: auth, session persistence, status polling, SSE, and ReportLab export.
"""

import server
from conftest import API_KEY_HEADERS, FakeGraph, seed_session


def _outline():
    return [
        {
            "section_id": "sec_1",
            "title": "Background",
            "description": "Context and prior work",
            "order": 1,
        },
        {
            "section_id": "sec_2",
            "title": "Findings",
            "description": "Key results",
            "order": 2,
        },
    ]


def _waiting_state():
    return {
        "next_agent": "WAIT_FOR_HUMAN",
        "is_complete": False,
        "outline": _outline(),
        "stream_updates": [],
        "topic": "Quantum computing advances",
        "depth": "quick",
    }


def _complete_state():
    return {
        "next_agent": "END",
        "is_complete": True,
        "topic": "Quantum computing advances",
        "depth": "quick",
        "stream_updates": [],
        "written_sections": [
            {
                "section_id": "sec_1",
                "title": "Background",
                "content": "Background prose with [1] citation.",
                "word_count": 42,
                "sources_used": ["https://example.com/a"],
            },
            {
                "section_id": "sec_2",
                "title": "Findings",
                "content": "Findings prose with [1] citation.",
                "word_count": 38,
                "sources_used": ["https://example.com/a"],
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
        "fact_check_results": [],
    }


def test_full_lifecycle_run_upload_approve_download(client, fake_db, monkeypatch):
    graph = FakeGraph(scripted_results=[_waiting_state(), _complete_state()])
    monkeypatch.setattr(server, "get_graph", lambda: graph)

    # 1. Upload a PDF before the run (no session yet — upload-only tracking row).
    upload = client.post(
        "/api/upload-pdf",
        files={"file": ("sources.pdf", b"%PDF-1.4 fake pdf body", "application/pdf")},
        headers=API_KEY_HEADERS,
    )
    assert upload.status_code == 200, upload.text
    pdf_path = upload.json()["path"]

    # 2. Start the run referencing the uploaded PDF — token issued here.
    run = client.post(
        "/api/run",
        json={"topic": "Quantum computing advances", "uploaded_pdfs": [pdf_path]},
        headers=API_KEY_HEADERS,
    )
    assert run.status_code == 200, run.text
    run_body = run.json()
    session_id = run_body["session_id"]
    session_token = run_body["session_token"]
    assert session_token

    # The background task runs inside the test client: graph paused for approval.
    assert fake_db[session_id]["data"]["status"] == "waiting_approval"

    # 3. Status polling works with the API key alone.
    status = client.get(f"/api/session/{session_id}/status", headers=API_KEY_HEADERS)
    assert status.status_code == 200
    assert status.json()["status"] == "waiting_approval"
    assert status.json()["has_outline"] is True

    # 4. Approving requires the session token — API key alone cannot do it.
    denied = client.post(
        "/api/approve-outline",
        json={"session_id": session_id, "outline": _outline()},
        headers=API_KEY_HEADERS,
    )
    assert denied.status_code == 401

    approved = client.post(
        "/api/approve-outline",
        json={"session_id": session_id, "outline": _outline()},
        headers={**API_KEY_HEADERS, "X-Session-Token": session_token},
    )
    assert approved.status_code == 200, approved.text

    # The resume background task completed the run.
    assert fake_db[session_id]["data"]["status"] == "complete"

    # 5. Downloading the PDF also requires the session token.
    download_denied = client.post(
        "/api/export-pdf", params={"session_id": session_id}, headers=API_KEY_HEADERS
    )
    assert download_denied.status_code == 401

    download = client.post(
        "/api/export-pdf",
        params={"session_id": session_id},
        headers={**API_KEY_HEADERS, "X-Session-Token": session_token},
    )
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "application/pdf"
    assert download.content[:5] == b"%PDF-"

    # 6. Full session details never expose the token hash.
    details = client.get(f"/api/session/{session_id}", headers=API_KEY_HEADERS)
    assert details.status_code == 200
    assert "token_hash" not in details.json()
    assert "session_token_hash" not in details.json()


def test_stream_delivers_update_and_state_events(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "get_graph", lambda: FakeGraph())
    sid = seed_session(
        fake_db,
        token="tok",
        status="waiting_approval",
        state={"stream_updates": ["hello"], "outline": []},
    )
    response = client.get(
        f"/api/session/{sid}/stream",
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert response.status_code == 200
    assert '"type": "update"' in response.text
    assert "hello" in response.text
    assert '"type": "state"' in response.text


def test_upload_rejects_non_pdf_files(client):
    response = client.post(
        "/api/upload-pdf",
        files={"file": ("notes.txt", b"plain text, not a pdf", "text/plain")},
        headers=API_KEY_HEADERS,
    )
    assert response.status_code == 400
