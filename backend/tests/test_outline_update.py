"""Tests for PUT /api/session/{id}/outline — editable outline before approval."""

import server
from conftest import API_KEY_HEADERS, FakeGraph, seed_session


def _outline():
    return [
        {"section_id": "sec_1", "title": "Background", "description": "Context", "order": 1},
        {"section_id": "sec_2", "title": "Findings", "description": "Key results", "order": 2},
    ]


def _edited_outline():
    return [
        {
            "section_id": "sec_1",
            "title": "Background and Prior Work",
            "description": "Context",
            "order": 1,
        },
        {"section_id": "sec_2", "title": "Findings", "description": "Key results", "order": 2},
    ]


def _waiting_session(store):
    return seed_session(
        store,
        token="tok",
        status="waiting_approval",
        state={"outline": _outline(), "stream_updates": []},
    )


def test_update_outline_requires_api_key(client, fake_db):
    sid = _waiting_session(fake_db)
    response = client.put(f"/api/session/{sid}/outline", json={"outline": _edited_outline()})
    assert response.status_code == 401


def test_update_outline_requires_session_token(client, fake_db):
    sid = _waiting_session(fake_db)
    response = client.put(
        f"/api/session/{sid}/outline",
        json={"outline": _edited_outline()},
        headers=API_KEY_HEADERS,
    )
    assert response.status_code == 401


def test_update_outline_rejects_wrong_token(client, fake_db):
    sid = _waiting_session(fake_db)
    response = client.put(
        f"/api/session/{sid}/outline",
        json={"outline": _edited_outline()},
        headers={**API_KEY_HEADERS, "X-Session-Token": "wrong"},
    )
    assert response.status_code == 403


def test_update_outline_unknown_session(client, fake_db):
    response = client.put(
        "/api/session/00000000-0000-0000-0000-000000000000/outline",
        json={"outline": _edited_outline()},
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert response.status_code == 404


def test_update_outline_rejects_non_waiting_session(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="complete", state={})
    response = client.put(
        f"/api/session/{sid}/outline",
        json={"outline": _edited_outline()},
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert response.status_code == 400


def test_update_outline_rejects_empty_outline(client, fake_db):
    sid = _waiting_session(fake_db)
    response = client.put(
        f"/api/session/{sid}/outline",
        json={"outline": []},
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert response.status_code == 400


def test_update_outline_persists_edits_and_keeps_gate(client, fake_db):
    sid = _waiting_session(fake_db)
    response = client.put(
        f"/api/session/{sid}/outline",
        json={"outline": _edited_outline()},
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "updated"
    assert body["outline"][0]["title"] == "Background and Prior Work"

    # Edits persisted into session state...
    persisted = fake_db[sid]["data"]["state"]["outline"]
    assert persisted[0]["title"] == "Background and Prior Work"
    # ...the approval gate is untouched (still waiting)...
    assert fake_db[sid]["data"]["status"] == "waiting_approval"
    # ...and the edit shows up in the trace log.
    assert any("Outline edited" in u for u in fake_db[sid]["data"]["state"]["stream_updates"])


def test_update_outline_orders_sections_by_order_field(client, fake_db):
    sid = _waiting_session(fake_db)
    reordered = [
        {"section_id": "sec_2", "title": "Findings", "description": "Key results", "order": 2},
        {"section_id": "sec_1", "title": "Background", "description": "Context", "order": 1},
    ]
    response = client.put(
        f"/api/session/{sid}/outline",
        json={"outline": reordered},
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert response.status_code == 200, response.text
    persisted = fake_db[sid]["data"]["state"]["outline"]
    assert [s["section_id"] for s in persisted] == ["sec_1", "sec_2"]


def test_update_then_approve_carries_edited_outline_into_checkpoint(
    client, fake_db, monkeypatch
):
    """The human-approval gate must receive the edited outline, not the original."""
    graph = FakeGraph(scripted_results=[{"next_agent": "END", "is_complete": True}])
    monkeypatch.setattr(server, "get_graph", lambda: graph)
    sid = _waiting_session(fake_db)

    updated = client.put(
        f"/api/session/{sid}/outline",
        json={"outline": _edited_outline()},
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert updated.status_code == 200, updated.text

    approved = client.post(
        "/api/approve-outline",
        json={"session_id": sid, "outline": _edited_outline()},
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert approved.status_code == 200, approved.text

    assert graph.state_updates[0]["outline_approved"] is True
    checkpoint_outline = graph.state_updates[0]["approved_outline"]
    assert checkpoint_outline[0]["title"] == "Background and Prior Work"
