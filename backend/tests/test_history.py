"""Tests for GET /api/history — the report-history dashboard listing."""

from conftest import API_KEY_HEADERS, seed_session


def test_history_requires_api_key(client, fake_db):
    response = client.get("/api/history")
    assert response.status_code == 401


def test_history_rejects_wrong_api_key(client, fake_db):
    response = client.get("/api/history", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_history_returns_sessions_newest_first(client, fake_db):
    older = seed_session(fake_db, token="tok-a", status="complete")
    newer = seed_session(fake_db, token="tok-b", status="waiting_approval")
    fake_db[older]["data"]["created_at"] = "2026-01-01T00:00:00+00:00"
    fake_db[newer]["data"]["created_at"] = "2026-06-01T00:00:00+00:00"

    response = client.get("/api/history", headers=API_KEY_HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    assert [s["id"] for s in body["sessions"]] == [newer, older]


def test_history_entries_are_metadata_only(client, fake_db):
    sid = seed_session(
        fake_db,
        token="tok",
        status="complete",
        state={"written_sections": [{"title": "secret report body"}]},
    )

    response = client.get("/api/history", headers=API_KEY_HEADERS)
    assert response.status_code == 200
    entry = response.json()["sessions"][0]
    assert entry["id"] == sid
    assert set(entry.keys()) == {
        "id",
        "topic",
        "depth",
        "status",
        "created_at",
        "updated_at",
    }
    # Report content and token material must never leak through the listing.
    assert "written_sections" not in response.text
    assert "token" not in response.text


def test_history_limit_and_offset_paging(client, fake_db):
    for i in range(5):
        sid = seed_session(fake_db, token=f"tok-{i}", status="complete")
        fake_db[sid]["data"]["created_at"] = f"2026-0{i + 1}-01T00:00:00+00:00"

    response = client.get(
        "/api/history", params={"limit": 2, "offset": 1}, headers=API_KEY_HEADERS
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    # Newest first, skipping the single newest session via offset.
    assert [s["created_at"] for s in body["sessions"]] == [
        "2026-04-01T00:00:00+00:00",
        "2026-03-01T00:00:00+00:00",
    ]


def test_history_rejects_out_of_range_limit(client, fake_db):
    response = client.get(
        "/api/history", params={"limit": 500}, headers=API_KEY_HEADERS
    )
    assert response.status_code == 422


def test_history_rejects_negative_offset(client, fake_db):
    response = client.get("/api/history", params={"offset": -1}, headers=API_KEY_HEADERS)
    assert response.status_code == 422


def test_history_empty_store_returns_empty_list(client, fake_db):
    response = client.get("/api/history", headers=API_KEY_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"count": 0, "sessions": []}
