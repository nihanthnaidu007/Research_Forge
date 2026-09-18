"""W4 mid-run steering: pause/resume/redirect endpoint, command consumption
under the per-session steer lock (exactly once), the write-vs-persist race,
slot reacquire on resume, and budget survival across a pause."""

import asyncio

import server
from conftest import API_KEY_HEADERS, FakeGraph, seed_session

from utils import token_budget

OWN = {**API_KEY_HEADERS, "X-Session-Token": "tok"}


def _approved_state(**overrides):
    state = {
        "topic": "Quantum computing",
        "outline_approved": True,
        "approved_outline": [
            {
                "section_id": "sec_1",
                "title": "Background",
                "description": "history of quantum research",
            },
            {
                "section_id": "sec_2",
                "title": "Applications",
                "description": "current applications of quantum computing",
            },
        ],
        "written_sections": [
            {
                "section_id": "sec_1",
                "title": "Background",
                "content": "...",
                "version": 1,
            },
            {
                "section_id": "sec_2",
                "title": "Applications",
                "content": "...",
                "version": 1,
            },
        ],
        "confidence_scores": {"sec_1": 0.5, "sec_2": 0.5},
        "stream_updates": [],
        "research_rounds": 0,
    }
    state.update(overrides)
    return state


def _complete_result():
    return {
        "next_agent": "END",
        "is_complete": True,
        "error": None,
        "written_sections": _approved_state()["written_sections"],
        "research_rounds": 0,
        "stream_updates": [],
    }


def _mk_config(sid):
    return {"configurable": {"thread_id": sid}}


# --- Endpoint validation ---


def test_steer_unknown_command_400(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="running", state=_approved_state())
    response = client.post(
        f"/api/session/{sid}/steer",
        json={"command": "rewind"},
        headers=OWN,
    )
    assert response.status_code == 400
    assert "pause, resume, or redirect" in response.json()["detail"]


def test_steer_requires_session_ownership(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="running", state=_approved_state())
    response = client.post(
        f"/api/session/{sid}/steer",
        json={"command": "pause"},
        headers=API_KEY_HEADERS,  # API key alone cannot steer
    )
    assert response.status_code == 401


def test_steer_unknown_session_404(client, fake_db):
    response = client.post(
        "/api/session/does-not-exist/steer",
        json={"command": "pause"},
        headers=OWN,
    )
    assert response.status_code == 404


def test_steer_pause_rejected_when_not_running(client, fake_db):
    sid = seed_session(
        fake_db, token="tok", status="waiting_approval", state=_approved_state()
    )
    response = client.post(
        f"/api/session/{sid}/steer", json={"command": "pause"}, headers=OWN
    )
    assert response.status_code == 400


def test_steer_pause_rejected_before_outline_approval(client, fake_db):
    sid = seed_session(
        fake_db,
        token="tok",
        status="running",
        state=_approved_state(outline_approved=False),
    )
    response = client.post(
        f"/api/session/{sid}/steer", json={"command": "pause"}, headers=OWN
    )
    assert response.status_code == 400
    # Nothing written into the session state.
    assert "pending_command" not in fake_db[sid]["data"]["state"]


def test_steer_redirect_requires_focus(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="running", state=_approved_state())
    response = client.post(
        f"/api/session/{sid}/steer",
        json={"command": "redirect", "focus": "   "},
        headers=OWN,
    )
    assert response.status_code == 400


def test_steer_redirect_allowed_while_paused(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="paused", state=_approved_state())
    response = client.post(
        f"/api/session/{sid}/steer",
        json={"command": "redirect", "focus": "focus on superconducting qubits"},
        headers=OWN,
    )
    assert response.status_code == 200
    assert fake_db[sid]["data"]["state"]["pending_command"]["command"] == "redirect"


def test_resume_rejected_when_not_paused(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="running", state=_approved_state())
    response = client.post(
        f"/api/session/{sid}/steer", json={"command": "resume"}, headers=OWN
    )
    assert response.status_code == 400


def test_steer_pause_accepted_and_written_under_lock(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="running", state=_approved_state())
    response = client.post(
        f"/api/session/{sid}/steer", json={"command": "pause"}, headers=OWN
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert fake_db[sid]["data"]["state"]["pending_command"] == {"command": "pause"}
    # Status unchanged: the pause takes effect at the next driver iteration.
    assert fake_db[sid]["data"]["status"] == "running"


# --- Slot semantics on resume ---


def test_resume_429_when_run_slots_saturated(client, fake_db, monkeypatch):
    sid = seed_session(fake_db, token="tok", status="paused", state=_approved_state())
    monkeypatch.setattr(server, "_graph_run_slots", asyncio.Semaphore(0))
    response = client.post(
        f"/api/session/{sid}/steer", json={"command": "resume"}, headers=OWN
    )
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["retry_after_seconds"] > 0
    assert response.headers.get("retry-after") == str(detail["retry_after_seconds"])
    # Still paused — a rejected resume must not flip the status.
    assert fake_db[sid]["data"]["status"] == "paused"


def test_pause_resume_lifecycle_with_slot_release(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "_graph_run_slots", asyncio.Semaphore(1))
    graph = FakeGraph(scripted_results=[_complete_result()])
    monkeypatch.setattr(server, "get_graph", lambda: graph)

    sid = seed_session(fake_db, token="tok", status="running", state=_approved_state())
    token_budget.ensure_budget(sid)

    # 1. Pause: command queued, then the driver parks the session.
    accepted = client.post(
        f"/api/session/{sid}/steer", json={"command": "pause"}, headers=OWN
    )
    assert accepted.status_code == 200
    asyncio.run(
        server._drive_post_approval_iterations(
            sid, graph, _mk_config(sid), fake_db[sid]["data"]
        )
    )
    assert fake_db[sid]["data"]["status"] == "paused"
    assert graph.invocations == []  # paused before the next invoke
    # Budget deliberately survives a pause (per-run semantics).
    assert token_budget.get_budget(sid) is not None

    # 2. Resume: reacquires the slot and drives to completion in-background.
    resumed = client.post(
        f"/api/session/{sid}/steer", json={"command": "resume"}, headers=OWN
    )
    assert resumed.status_code == 200
    # Background task ran inside the test client.
    assert fake_db[sid]["data"]["status"] == "complete"
    # True completion releases the budget.
    assert token_budget.get_budget(sid) is None
    # The slot was released — a fresh run is accepted again.
    run = client.post("/api/run", json={"topic": "slot check"}, headers=API_KEY_HEADERS)
    assert run.status_code == 200


# --- Command consumption: exactly once, redirect merge ---


def test_redirect_consumed_exactly_once(client, fake_db):
    sid = seed_session(
        fake_db,
        token="tok",
        status="running",
        state=_approved_state(
            pending_command={"command": "redirect", "focus": "quantum research history"}
        ),
    )
    graph = FakeGraph(scripted_results=[_complete_result()])

    payload = asyncio.run(server._consume_pending_command(sid, graph, _mk_config(sid)))
    assert payload["command"] == "redirect"
    # Second consume: nothing left.
    again = asyncio.run(server._consume_pending_command(sid, graph, _mk_config(sid)))
    assert again is None
    # The checkpoint merge ran exactly once.
    merges = [u for u in graph.state_updates if "redirect_focus" in u]
    assert len(merges) == 1
    assert fake_db[sid]["data"]["state"].get("pending_command") is None


def test_redirect_merges_focus_and_flags_targeted_sections(client, fake_db):
    sid = seed_session(
        fake_db,
        token="tok",
        status="running",
        state=_approved_state(
            pending_command={"command": "redirect", "focus": "quantum research history"}
        ),
    )
    graph = FakeGraph(scripted_results=[_complete_result()])

    asyncio.run(server._consume_pending_command(sid, graph, _mk_config(sid)))

    state = fake_db[sid]["data"]["state"]
    assert "quantum research history" in state["redirect_focus"]
    # sec_1 ("history of quantum research") matches by keyword overlap.
    assert state["sections_needing_rewrite"] == ["sec_1"]
    assert [s["section_id"] for s in state["written_sections"]] == ["sec_2"]
    assert state["current_section_index"] == 0
    assert any("Redirect applied" in u for u in state["stream_updates"])
    # The same updates reached the graph checkpoint (invoke(None) resumes
    # from the checkpoint, not the session row).
    merge = next(u for u in graph.state_updates if "redirect_focus" in u)
    assert merge["sections_needing_rewrite"] == ["sec_1"]


def test_redirect_without_keyword_match_targets_weakest_section(client, fake_db):
    sid = seed_session(
        fake_db,
        token="tok",
        status="running",
        state=_approved_state(
            confidence_scores={"sec_1": 0.9, "sec_2": 0.2},
            pending_command={
                "command": "redirect",
                "focus": "elucidate entirely unrelated matters",
            },
        ),
    )
    graph = FakeGraph(scripted_results=[_complete_result()])

    asyncio.run(server._consume_pending_command(sid, graph, _mk_config(sid)))

    state = fake_db[sid]["data"]["state"]
    # No keyword match → deterministic fallback: weakest written section.
    assert state["sections_needing_rewrite"] == ["sec_2"]


# --- Write-vs-persist race (R3) ---


def test_pending_command_survives_iteration_persist(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="running", state=_approved_state())
    graph = FakeGraph(scripted_results=[_complete_result()])

    # Command written mid-iteration (endpoint), then the iteration result —
    # which knows nothing about commands — persists under the same lock.
    asyncio.run(server._write_pending_command(sid, {"command": "pause"}))
    asyncio.run(
        server._persist_iteration_state(
            sid, {"next_agent": "synthesis", "stream_updates": []}
        )
    )
    assert fake_db[sid]["data"]["state"]["pending_command"] == {"command": "pause"}

    # Consumption clears it; a later persist must not resurrect it.
    consumed = asyncio.run(server._consume_pending_command(sid, graph, _mk_config(sid)))
    assert consumed["command"] == "pause"
    asyncio.run(
        server._persist_iteration_state(
            sid, {"next_agent": "synthesis", "stream_updates": []}
        )
    )
    assert fake_db[sid]["data"]["state"].get("pending_command") is None


def test_old_sessions_without_command_field_work(client, fake_db):
    """Pre-W4 sessions have no pending_command key anywhere — reads must
    default cleanly instead of raising KeyError."""
    sid = seed_session(fake_db, token="tok", status="running", state=_approved_state())
    graph = FakeGraph(scripted_results=[_complete_result()])

    payload = asyncio.run(server._consume_pending_command(sid, graph, _mk_config(sid)))
    assert payload is None

    persisted = asyncio.run(
        server._persist_iteration_state(
            sid, {"next_agent": "synthesis", "stream_updates": []}
        )
    )
    assert "pending_command" not in persisted


# --- Rate limiting ---


def test_steer_endpoint_rate_limited(client, fake_db):
    server.limiter.enabled = True
    try:
        sid = seed_session(
            fake_db, token="tok", status="running", state=_approved_state()
        )
        last_response = None
        for _ in range(31):  # limit is 30/minute
            last_response = client.post(
                f"/api/session/{sid}/steer", json={"command": "pause"}, headers=OWN
            )
        assert last_response.status_code == 429
    finally:
        server.limiter.enabled = False
