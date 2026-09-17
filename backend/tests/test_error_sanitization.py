"""Error sanitization: graph failures must not persist raw exception text
into client-visible session state (server.py run + resume handlers)."""

import server
from conftest import API_KEY_HEADERS, seed_session


class _ExplodingGraph:
    def invoke(self, state, config):
        raise RuntimeError(
            "boom — https://internal-proxy.example/secret_paths leaked detail"
        )

    def update_state(self, config, updates):
        pass


def test_initial_run_failure_is_sanitized(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "get_graph", lambda: _ExplodingGraph())
    response = client.post(
        "/api/run",
        json={"topic": "Sanitize initial run failure"},
        headers=API_KEY_HEADERS,
    )
    assert response.status_code == 200
    sid = response.json()["session_id"]
    data = fake_db[sid]["data"]
    assert data["status"] == "error"
    error = data["state"]["error"]
    assert error.startswith("Report generation failed due to an internal error")
    assert "Reference:" in error
    assert "boom" not in error
    assert "leaked detail" not in error
    assert "example" not in error


def test_resume_failure_is_sanitized(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "get_graph", lambda: _ExplodingGraph())
    sid = seed_session(fake_db, token="tok", status="waiting_approval")
    response = client.post(
        "/api/approve-outline",
        json={"session_id": sid, "outline": []},
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert response.status_code == 200
    data = fake_db[sid]["data"]
    assert data["status"] == "error"
    error = data["state"]["error"]
    assert error.startswith("Report generation failed due to an internal error")
    assert "boom" not in error
    assert "leaked detail" not in error
