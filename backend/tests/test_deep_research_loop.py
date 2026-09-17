"""W4 deep-research loop: deterministic gap signal, round bookkeeping,
post-approval driver-loop termination at the round/iteration cap, budget
accounting across rounds, and old-checkpoint compatibility.

The supervisor rule under test is pure routing: no LLM call is involved
anywhere in this file (FakeGraph + fake_db from conftest).
"""

import asyncio

import pytest
import server
from conftest import API_KEY_HEADERS, FakeGraph, seed_session

from graph import supervisor
from graph.state import create_initial_state
from utils import token_budget


def _full_state(**overrides):
    """A fully written, post-approval state with weak coverage."""
    state = {
        "topic": "Quantum computing",
        "outline_approved": True,
        "approved_outline": [
            {
                "section_id": "sec_1",
                "title": "Background",
                "description": "foundational context and prior work",
            },
            {
                "section_id": "sec_2",
                "title": "Applications",
                "description": "current applications of quantum computing",
            },
        ],
        "outline": [
            {
                "section_id": "sec_1",
                "title": "Background",
                "description": "foundational context and prior work",
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
        "confidence_scores": {"sec_1": 0.4, "sec_2": 0.45},
        "overall_confidence": 0.42,
        "fact_check_results": [
            {
                "section_id": "sec_1",
                "verdict": "SUPPORTED",
                "claim": "background claims",
            },
            {
                "section_id": "sec_2",
                "verdict": "UNSUPPORTED",
                "claim": "applications of quantum computing today",
            },
        ],
        "research_results": [{"url": "https://example.com/a", "title": "A"}],
        "research_rounds": 0,
        "coverage_gaps": [],
        "stream_updates": [],
        "completed_agents": ["synthesis", "factcheck"],
        "is_complete": False,
    }
    state.update(overrides)
    return state


# --- Gap-trip thresholds (deterministic, no LLM) ---


def test_gap_trips_below_confidence_threshold(monkeypatch):
    monkeypatch.setenv("RESEARCH_GAP_CONFIDENCE_THRESHOLD", "0.6")
    tripped, gaps, affected = supervisor.evaluate_coverage_gaps(_full_state())
    assert tripped
    assert gaps
    assert affected
    assert set(affected) <= {"sec_1", "sec_2"}


def test_no_trip_when_confidence_high(monkeypatch):
    monkeypatch.setenv("RESEARCH_GAP_CONFIDENCE_THRESHOLD", "0.6")
    state = _full_state(
        overall_confidence=0.9,
        confidence_scores={"sec_1": 0.9, "sec_2": 0.88},
        fact_check_results=[
            {
                "section_id": "sec_1",
                "verdict": "SUPPORTED",
                "claim": "background claims",
            }
        ],
    )
    tripped, gaps, _affected = supervisor.evaluate_coverage_gaps(state)
    assert not tripped
    assert gaps == []


def test_gap_trips_on_unsupported_rate_above_one(monkeypatch):
    """Unsupported-verdict share above the cap trips a round even when
    confidence scores look fine."""
    monkeypatch.setenv("RESEARCH_GAP_CONFIDENCE_THRESHOLD", "0.1")
    monkeypatch.setenv("RESEARCH_GAP_UNSUPPORTED_RATE", "0.3")
    state = _full_state(
        overall_confidence=0.9,
        confidence_scores={"sec_1": 0.9, "sec_2": 0.9},
        fact_check_results=[
            {
                "section_id": "sec_1",
                "verdict": "SUPPORTED",
                "claim": "background claims",
            },
            {
                "section_id": "sec_2",
                "verdict": "UNSUPPORTED",
                "claim": "applications of quantum computing today",
            },
        ],  # 1/2 = 50% unsupported > 30% cap
    )
    tripped, gaps, affected = supervisor.evaluate_coverage_gaps(state)
    assert tripped
    assert affected == ["sec_2"]
    assert any("UNSUPPORTED" in gap for gap in gaps)


def test_thresholds_are_env_tunable(monkeypatch):
    monkeypatch.setenv("RESEARCH_GAP_CONFIDENCE_THRESHOLD", "0.1")
    monkeypatch.setenv("RESEARCH_GAP_UNSUPPORTED_RATE", "0.99")
    tripped, _gaps, _affected = supervisor.evaluate_coverage_gaps(_full_state())
    assert not tripped  # 0.42 confidence above 0.1; 50% unsupported below 99%


# --- Supervisor rule: route to research only pre-cap ---


def test_supervisor_routes_written_report_with_gaps_to_research(monkeypatch):
    monkeypatch.setenv("RESEARCH_GAP_CONFIDENCE_THRESHOLD", "0.6")
    monkeypatch.setenv("MAX_RESEARCH_ROUNDS", "2")
    decision = supervisor.get_supervisor_decision(_full_state())
    assert decision.next_agent == "research"
    assert "round 1" in decision.reasoning


def test_supervisor_never_exceeds_round_cap(monkeypatch):
    monkeypatch.setenv("RESEARCH_GAP_CONFIDENCE_THRESHOLD", "0.6")
    monkeypatch.setenv("MAX_RESEARCH_ROUNDS", "2")
    decision = supervisor.get_supervisor_decision(_full_state(research_rounds=2))
    # At the cap the loop must terminate — citations is the next step.
    assert decision.next_agent != "research"


def test_supervisor_skips_gap_rule_after_citations(monkeypatch):
    """Once the citations pass ran, the report is final — no re-research."""
    monkeypatch.setenv("RESEARCH_GAP_CONFIDENCE_THRESHOLD", "0.6")
    state = _full_state(completed_agents=["synthesis", "factcheck", "citations"])
    decision = supervisor.get_supervisor_decision(state)
    assert decision.next_agent != "research"


# --- apply_research_round: flag BEFORE re-synthesis (R6) ---


def test_apply_research_round_flags_affected_sections():
    state = _full_state()
    gaps = ["Low confidence (0.40) in section 'Background'"]
    supervisor.apply_research_round(state, gaps, ["sec_1"])

    assert state["research_rounds"] == 1
    assert state["coverage_gaps"] == gaps
    assert "sec_1" in state["sections_needing_rewrite"]
    assert "sec_1" in state["changed_section_ids"]
    # Flagged section pruned from written_sections so the supervisor's
    # sections-remaining rule routes back into synthesis.
    assert [s["section_id"] for s in state["written_sections"]] == ["sec_2"]
    assert state["current_section_index"] == 0
    assert "synthesis" not in state["completed_agents"]
    assert state["stream_updates"][-1].startswith("[")  # visible progress line


def test_apply_research_round_deduplicates_flags():
    state = _full_state(
        changed_section_ids=["sec_1"], sections_needing_rewrite=["sec_1"]
    )
    supervisor.apply_research_round(state, ["gap"], ["sec_1"])
    assert state["sections_needing_rewrite"].count("sec_1") == 1
    assert state["changed_section_ids"].count("sec_1") == 1


# --- Round-scoped queries ---


def test_build_round_queries_derive_from_gaps():
    gaps = [
        "Low confidence (0.40) in section 'Applications' — needs stronger sourcing",
        "1 of 2 claims UNSUPPORTED (50% > 30%): applications of quantum computing today",
    ]
    queries = supervisor_build_queries(gaps)
    assert queries
    assert all(isinstance(q, str) and q.strip() for q in queries)


def supervisor_build_queries(gaps):
    from graph.agents.research import build_round_queries

    return build_round_queries("Quantum computing", gaps)


# --- Old-checkpoint compatibility (pre-W4 states lack the new keys) ---


def test_old_checkpoint_state_works_end_to_end():
    """A state persisted before W4 (no research_rounds/coverage_gaps/
    redirect_focus keys) must load and route without KeyError."""
    state = _full_state()
    for key in ("research_rounds", "coverage_gaps", "redirect_focus"):
        state.pop(key, None)

    tripped, gaps, affected = supervisor.evaluate_coverage_gaps(state)
    assert tripped  # defaults: rounds 0 < cap
    if affected:
        supervisor.apply_research_round(state, gaps, affected)
        assert state["research_rounds"] == 1


def test_old_checkpoint_with_none_channel_values_routes_safely():
    """LangGraph materializes channels that a pre-W4 checkpoint never wrote
    as explicit None values — state.get(key, default) does NOT replace those.
    A live dogfood run caught exactly this, so every W4 reader must treat
    None like 'absent'."""
    state = _full_state()
    state["research_rounds"] = None
    state["coverage_gaps"] = None
    state["redirect_focus"] = None

    tripped, gaps, affected = supervisor.evaluate_coverage_gaps(state)
    assert tripped
    supervisor.apply_research_round(state, gaps, affected)
    assert state["research_rounds"] == 1
    assert isinstance(state["coverage_gaps"], list) and state["coverage_gaps"]


def test_create_initial_state_never_materializes_none_w4_fields():
    """Fresh runs start with concrete W4 defaults so live channels and the
    status endpoint report numbers/lists, never None."""
    initial = create_initial_state("dogfood topic")
    assert initial["research_rounds"] == 0
    assert initial["coverage_gaps"] == []
    assert initial["redirect_focus"] == ""


def test_status_endpoint_reports_round_fields_for_old_sessions(
    client, fake_db, monkeypatch
):
    monkeypatch.setattr(server, "get_graph", lambda: FakeGraph())
    sid = seed_session(
        fake_db, token="tok", status="complete", state={"stream_updates": []}
    )
    response = client.get(f"/api/session/{sid}/status", headers=API_KEY_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["research_rounds"] == 0
    assert body["coverage_gaps"] == []


# --- Post-approval driver loop: termination and budgets ---


def _never_ending_result(base_state):
    return {
        "next_agent": "synthesis",
        "is_complete": False,
        "error": None,
        "written_sections": base_state["written_sections"],
        "approved_outline": base_state["approved_outline"],
        "research_rounds": 0,
        "stream_updates": [],
    }


class _NeverEndingGraph(FakeGraph):
    """Scripted graph that never reaches END — the loop must stop itself."""

    def __init__(self, base_state):
        super().__init__()
        self.base_state = base_state

    def invoke(self, state, config):
        super().invoke(state, config)
        return _never_ending_result(self.base_state)


def _mk_config(sid):
    return {"configurable": {"thread_id": sid}}


def test_driver_loop_terminates_at_iteration_cap(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="running", state=_full_state())
    graph = _NeverEndingGraph(_full_state())
    session = fake_db[sid]["data"]

    asyncio.run(
        server._drive_post_approval_iterations(sid, graph, _mk_config(sid), session)
    )

    stored = fake_db[sid]["data"]
    assert stored["status"] == "error"
    assert "did not complete" in stored["state"]["error"]
    # The cap bounds the loop; the fake graph cannot run it forever.
    assert len(graph.invocations) < 60


def test_driver_releases_budget_on_completion(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="running", state=_full_state())
    graph = FakeGraph(
        scripted_results=[
            {
                "next_agent": "END",
                "is_complete": True,
                "written_sections": _full_state()["written_sections"],
                "research_rounds": 0,
                "stream_updates": [],
            }
        ]
    )
    token_budget.ensure_budget(sid)
    session = fake_db[sid]["data"]

    asyncio.run(
        server._drive_post_approval_iterations(sid, graph, _mk_config(sid), session)
    )

    assert fake_db[sid]["data"]["status"] == "complete"
    # True completion — per-run and round budgets released (W4 semantics).
    assert token_budget.get_budget(sid) is None
    assert token_budget.get_round_budget(sid) is None


def test_driver_releases_budget_on_error(client, fake_db):
    sid = seed_session(fake_db, token="tok", status="running", state=_full_state())
    graph = FakeGraph(
        scripted_results=[
            {
                "next_agent": "synthesis",
                "is_complete": False,
                "error": "synthesis exploded",
                "written_sections": [],
                "research_rounds": 0,
                "stream_updates": [],
            }
        ]
    )
    token_budget.ensure_budget(sid)
    session = fake_db[sid]["data"]

    asyncio.run(
        server._drive_post_approval_iterations(sid, graph, _mk_config(sid), session)
    )

    stored = fake_db[sid]["data"]
    assert stored["status"] == "error"
    assert token_budget.get_budget(sid) is None
    assert token_budget.get_round_budget(sid) is None


# --- Budget accounting across rounds (W4 per-run semantics) ---


def test_run_budget_persists_across_rounds():
    token_budget.release_budget("sess-rounds")
    token_budget.release_round_budget("sess-rounds")
    run_budget = token_budget.ensure_budget("sess-rounds")
    ctx = token_budget.set_session_context("sess-rounds")
    try:
        token_budget.record_usage_for_current_session(300)
        # Round 1 trips: fresh sub-cap, run budget keeps accumulating.
        token_budget.release_round_budget("sess-rounds")
        round_budget = token_budget.ensure_round_budget("sess-rounds")
        token_budget.record_usage_for_current_session(200)
        assert run_budget.used_tokens == 500
        assert round_budget.used_tokens == 200
        # Round 2 trips: sub-cap resets, run budget does not.
        token_budget.release_round_budget("sess-rounds")
        round_budget2 = token_budget.ensure_round_budget("sess-rounds")
        token_budget.record_usage_for_current_session(150)
        assert round_budget2.used_tokens == 150
        assert round_budget2 is not round_budget
        assert run_budget.used_tokens == 650
    finally:
        token_budget.reset_session_context(ctx)
        token_budget.release_budget("sess-rounds")
        token_budget.release_round_budget("sess-rounds")


def test_round_subcap_enforced_independently(monkeypatch):
    monkeypatch.setenv("RESEARCH_ROUND_TOKEN_BUDGET", "100")
    token_budget.release_budget("sess-subcap")
    token_budget.release_round_budget("sess-subcap")
    run_budget = token_budget.ensure_budget("sess-subcap")  # unlimited run budget
    token_budget.ensure_round_budget("sess-subcap")  # 100-token sub-cap
    ctx = token_budget.set_session_context("sess-subcap")
    try:
        token_budget.record_usage_for_current_session(60)
        with pytest.raises(token_budget.TokenBudgetExceeded):
            token_budget.record_usage_for_current_session(60)  # 120 > 100 sub-cap
        assert run_budget.used_tokens == 120  # run ledger still recorded
        assert run_budget.max_tokens is None
    finally:
        token_budget.reset_session_context(ctx)
        token_budget.release_budget("sess-subcap")
        token_budget.release_round_budget("sess-subcap")


def test_round_budget_unset_means_unlimited(monkeypatch):
    monkeypatch.setenv("RESEARCH_ROUND_TOKEN_BUDGET", "")
    token_budget.release_round_budget("sess-nocap")
    round_budget = token_budget.ensure_round_budget("sess-nocap")
    try:
        round_budget.record(10**9)
        assert round_budget.used_tokens == 10**9
    finally:
        token_budget.release_round_budget("sess-nocap")


def test_chat_registry_isolated_from_round_accounting():
    token_budget.release_budget("sess-iso")
    token_budget.release_round_budget("sess-iso")
    token_budget.release_chat_budget("sess-iso")
    try:
        chat_budget = token_budget.ensure_chat_budget("sess-iso")
        round_budget = token_budget.ensure_round_budget("sess-iso")
        ctx = token_budget.set_session_context("sess-iso")
        try:
            token_budget.record_usage_for_current_session(10)
        finally:
            token_budget.reset_session_context(ctx)
        assert round_budget.used_tokens == 10
        assert chat_budget.used_tokens == 0  # chat never sees graph usage
    finally:
        token_budget.release_budget("sess-iso")
        token_budget.release_round_budget("sess-iso")
        token_budget.release_chat_budget("sess-iso")
