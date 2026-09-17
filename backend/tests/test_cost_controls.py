"""Cost controls: global concurrency cap (429 with retry guidance) and the
per-run token budget accounting hook."""

import asyncio

import pytest
import server
from conftest import API_KEY_HEADERS, FakeGraph, seed_session

import utils.clients as clients
from utils import token_budget


def test_run_rejected_with_429_when_at_capacity(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "_graph_run_slots", asyncio.Semaphore(0))
    response = client.post(
        "/api/run", json={"topic": "Cost cap rejects new runs"}, headers=API_KEY_HEADERS
    )
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["retry_after_seconds"] > 0
    assert response.headers.get("retry-after") == str(detail["retry_after_seconds"])
    # Rejected runs must not create a session row.
    assert len(fake_db) == 0


def test_approve_rejected_with_429_when_at_capacity(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "_graph_run_slots", asyncio.Semaphore(0))
    sid = seed_session(fake_db, token="tok", status="waiting_approval")
    response = client.post(
        "/api/approve-outline",
        json={"session_id": sid, "outline": []},
        headers={**API_KEY_HEADERS, "X-Session-Token": "tok"},
    )
    assert response.status_code == 429


def test_run_slot_released_after_completion(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "_graph_run_slots", asyncio.Semaphore(1))
    monkeypatch.setattr(server, "get_graph", lambda: FakeGraph())
    first = client.post(
        "/api/run", json={"topic": "Slot release check one"}, headers=API_KEY_HEADERS
    )
    assert first.status_code == 200
    # The first run consumed and released its slot — the second is accepted.
    second = client.post(
        "/api/run", json={"topic": "Slot release check two"}, headers=API_KEY_HEADERS
    )
    assert second.status_code == 200


class _ExplodingGraph:
    def invoke(self, state, config):
        raise RuntimeError("boom — provider detail that must never reach a client")

    def update_state(self, config, updates):
        pass


def test_run_slot_released_when_run_fails(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "_graph_run_slots", asyncio.Semaphore(1))
    monkeypatch.setattr(server, "get_graph", lambda: _ExplodingGraph())
    first = client.post(
        "/api/run", json={"topic": "Slot release on failure"}, headers=API_KEY_HEADERS
    )
    assert first.status_code == 200
    sid = first.json()["session_id"]
    assert fake_db[sid]["data"]["status"] == "error"
    # finally-block freed the slot — a subsequent run is accepted.
    second = client.post(
        "/api/run", json={"topic": "Slot reuse after failure"}, headers=API_KEY_HEADERS
    )
    assert second.status_code == 200


def test_token_budget_exhaustion_stops_run_with_sanitized_error(
    client, fake_db, monkeypatch
):
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "100")

    class _BudgetBurningGraph(FakeGraph):
        def invoke(self, state, config):
            token_budget.record_usage_for_current_session(150)
            return super().invoke(state, config)

    monkeypatch.setattr(server, "get_graph", lambda: _BudgetBurningGraph())
    response = client.post(
        "/api/run", json={"topic": "Budget exhaustion stops run"}, headers=API_KEY_HEADERS
    )
    assert response.status_code == 200
    sid = response.json()["session_id"]
    data = fake_db[sid]["data"]
    assert data["status"] == "error"
    assert "token budget" in data["state"]["error"]
    # The budget entry is released with the phase — no registry leak.
    assert token_budget.get_budget(sid) is None


def test_budget_registry_released_after_successful_run(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "get_graph", lambda: FakeGraph())
    response = client.post(
        "/api/run", json={"topic": "Registry cleanup check"}, headers=API_KEY_HEADERS
    )
    assert response.status_code == 200
    sid = response.json()["session_id"]
    assert token_budget.get_budget(sid) is None


# --- Unit tests for the budget hook itself ---


def test_run_token_budget_records_and_enforces():
    budget = token_budget.RunTokenBudget(100)
    assert budget.record(60) == 60
    with pytest.raises(token_budget.TokenBudgetExceeded):
        budget.record(60)
    assert budget.used_tokens == 120


def test_unlimited_budget_never_raises():
    budget = token_budget.RunTokenBudget(None)
    budget.record(10**9)
    assert budget.used_tokens == 10**9


def test_record_usage_without_session_context_is_noop():
    token_budget.record_usage_for_current_session(500)  # must not raise


def test_max_tokens_from_env(monkeypatch):
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "500")
    assert token_budget._max_tokens_from_env() == 500
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "")
    assert token_budget._max_tokens_from_env() is None
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "0")
    assert token_budget._max_tokens_from_env() is None
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "-3")
    assert token_budget._max_tokens_from_env() is None
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "bogus")
    assert token_budget._max_tokens_from_env() is None


def test_parse_positive_int_env(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "5")
    assert server._parse_positive_int_env("MAX_CONCURRENT_RUNS", 3) == 5
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "0")
    assert server._parse_positive_int_env("MAX_CONCURRENT_RUNS", 3) == 3
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "abc")
    assert server._parse_positive_int_env("MAX_CONCURRENT_RUNS", 3) == 3


# --- Usage recording through the OpenAI call wrapper ---


class _FakeUsage:
    def __init__(self, total_tokens):
        self.total_tokens = total_tokens


class _FakeResponse:
    def __init__(self, total_tokens):
        self.usage = _FakeUsage(total_tokens)


class _FakeCompletions:
    def __init__(self, total_tokens):
        self.total_tokens = total_tokens
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _FakeResponse(self.total_tokens)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAIClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


def test_chat_completion_with_usage_records_against_active_budget(monkeypatch):
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "1000")
    completions = _FakeCompletions(total_tokens=42)
    monkeypatch.setattr(clients, "get_openai_client", lambda: _FakeOpenAIClient(completions))

    budget = token_budget.ensure_budget("sess-usage")
    ctx = token_budget.set_session_context("sess-usage")
    try:
        response = clients.chat_completion_with_usage(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )
        assert response.usage.total_tokens == 42
        assert completions.kwargs["model"] == "gpt-4o"
        assert budget.used_tokens == 42
    finally:
        token_budget.reset_session_context(ctx)
        token_budget.release_budget("sess-usage")
