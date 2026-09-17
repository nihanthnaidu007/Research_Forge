"""Auth-negative tests: every route rejects unauthenticated access, fails
closed when the API key is unset, and enforces per-session ownership."""

import pytest
from conftest import API_KEY_HEADERS, TEST_API_KEY, seed_session

UNKNOWN_SESSION = "11111111-1111-1111-1111-111111111111"


def _route_cases():
    upload = {"files": {"file": ("probe.pdf", b"%PDF-probe", "application/pdf")}}
    return [
        ("root", "GET", "/api/", {}),
        ("run", "POST", "/api/run", {"json": {"topic": "Auth negative test topic"}}),
        ("status", "GET", f"/api/session/{UNKNOWN_SESSION}/status", {}),
        ("details", "GET", f"/api/session/{UNKNOWN_SESSION}", {}),
        ("stream", "GET", f"/api/session/{UNKNOWN_SESSION}/stream", {}),
        (
            "approve",
            "POST",
            "/api/approve-outline",
            {"json": {"session_id": UNKNOWN_SESSION, "outline": []}},
        ),
        ("upload", "POST", "/api/upload-pdf", upload),
        (
            "export",
            "POST",
            "/api/export-pdf",
            {"params": {"session_id": UNKNOWN_SESSION}},
        ),
    ]


ROUTE_IDS = [case[0] for case in _route_cases()]


@pytest.mark.parametrize(("name", "method", "path", "kwargs"), _route_cases(), ids=ROUTE_IDS)
def test_missing_api_key_rejected(client, name, method, path, kwargs):
    response = client.request(method, path, **kwargs)
    assert response.status_code == 401, f"{method} {path}"


@pytest.mark.parametrize(("name", "method", "path", "kwargs"), _route_cases(), ids=ROUTE_IDS)
def test_wrong_api_key_rejected(client, name, method, path, kwargs):
    response = client.request(
        method, path, headers={"X-API-Key": "wrong-key"}, **kwargs
    )
    assert response.status_code == 401, f"{method} {path}"


@pytest.mark.parametrize(("name", "method", "path", "kwargs"), _route_cases(), ids=ROUTE_IDS)
def test_fails_closed_when_api_key_unset(
    client, monkeypatch, name, method, path, kwargs
):
    monkeypatch.delenv("RESEARCHFORGE_API_KEY")
    response = client.request(method, path, headers=API_KEY_HEADERS, **kwargs)
    assert response.status_code == 503, f"{method} {path}"


def test_health_stays_open_without_api_key(client, monkeypatch):
    """Health must answer without credentials even when the DB layer is stubbed."""
    import db

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query):
            return None

    class _FakePool:
        def connection(self):
            return _FakeConn()

    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_bearer_authorization_accepted_on_root(client):
    response = client.get("/api/", headers={"Authorization": f"Bearer {TEST_API_KEY}"})
    assert response.status_code == 200


def _ownership_cases(sid):
    return [
        ("GET", f"/api/session/{sid}/stream", {}),
        (
            "POST",
            "/api/approve-outline",
            {"json": {"session_id": sid, "outline": []}},
        ),
        ("POST", "/api/export-pdf", {"params": {"session_id": sid}}),
    ]


def test_missing_session_token_rejected(client, fake_db):
    for method, path, kwargs in _ownership_cases(UNKNOWN_SESSION):
        response = client.request(method, path, headers=API_KEY_HEADERS, **kwargs)
        assert response.status_code == 401, f"{method} {path}"


def test_wrong_session_token_rejected(client, fake_db):
    sid = seed_session(fake_db, token="correct-token")
    for method, path, kwargs in _ownership_cases(sid):
        response = client.request(
            method,
            path,
            headers={**API_KEY_HEADERS, "X-Session-Token": "wrong-token"},
            **kwargs,
        )
        assert response.status_code == 403, f"{method} {path}"


def test_unknown_session_with_token_returns_404(client, fake_db):
    response = client.get(
        f"/api/session/{UNKNOWN_SESSION}/stream",
        headers={**API_KEY_HEADERS, "X-Session-Token": "some-token"},
    )
    assert response.status_code == 404


def test_valid_session_token_passes_ownership(client, fake_db):
    sid = seed_session(fake_db, token="correct-token", status="waiting_approval")
    response = client.get(
        f"/api/session/{sid}/stream",
        headers={**API_KEY_HEADERS, "X-Session-Token": "correct-token"},
    )
    assert response.status_code == 200
    assert '"type": "state"' in response.text


def test_session_token_via_query_param_accepted(client, fake_db):
    sid = seed_session(fake_db, token="correct-token", status="waiting_approval")
    response = client.get(
        f"/api/session/{sid}/stream?session_token=correct-token",
        headers=API_KEY_HEADERS,
    )
    assert response.status_code == 200
