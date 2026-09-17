"""Chat-with-report endpoint tests (W3).

Covers the spec D1.5 matrix against the faked chat seam (no real OpenAI):
auth (401/403/404/503), complete-gate, grounding-prompt assembly, citation
validation (resolved + unresolved), per-turn budget 429, TTL-404, and the
old-session state round-trip.
"""

import asyncio
from types import SimpleNamespace

import pytest
import server
from conftest import API_KEY_HEADERS, seed_session

import chat as chat_module
import utils.token_budget as token_budget

OWNED_HEADERS = {**API_KEY_HEADERS, "X-Session-Token": "tok"}

ANSWER_TEXT = "The report says transformers scaled data [1]."

COMPLETE_STATE = {
    "topic": "Quantum computing advances",
    "depth": "quick",
    "written_sections": [
        {
            "section_id": "sec_1",
            "title": "Background",
            "content": "Background prose about qubits [1].",
            "word_count": 10,
            "sources_used": ["https://example.com/a"],
        },
    ],
    "sources": [
        {
            "url": "https://example.com/a",
            "title": "Example Source",
            "domain": "example.com",
            "citation_number": 1,
            "snippet": "Qubits are the unit of quantum information.",
            "integrity_status": "verified",
            "retracted": False,
            "citation_count": 12,
        },
    ],
    "confidence_scores": {},
    "overall_confidence": 0.9,
}


class _FakeChatCompletion:
    """Stand-in for the OpenAI chat completion response object."""

    def __init__(self, content: str, total_tokens: int):
        self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=max(total_tokens - 100, 0),
            total_tokens=total_tokens,
        )


@pytest.fixture
def fake_chat_llm(monkeypatch):
    """Replace the chat module's LLM seam; records prompts, returns an answer.

    Mirrors the real wrapper's usage-recording behavior so budget tests
    exercise the same TokenBudgetExceeded path as production.
    """
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append(kwargs)
        response = _FakeChatCompletion(ANSWER_TEXT, 500)
        if response.usage.total_tokens:
            token_budget.record_usage_for_current_session(
                int(response.usage.total_tokens)
            )
        return response

    monkeypatch.setattr(chat_module, "chat_completion_with_usage", _fake)
    return calls


def _seed_complete(store, state=None):
    return seed_session(
        store,
        token="tok",
        status="complete",
        state=state if state is not None else COMPLETE_STATE,
    )


def _chat(client, sid, message="What did the report find?", headers=OWNED_HEADERS):
    return client.post(
        f"/api/session/{sid}/chat", json={"message": message}, headers=headers
    )


# --- Auth matrix: fail-closed on every layer ---------------------------------


def test_chat_requires_api_key(client, fake_db, fake_chat_llm):
    sid = _seed_complete(fake_db)
    response = client.post(f"/api/session/{sid}/chat", json={"message": "hi"})
    assert response.status_code == 401


def test_chat_fails_closed_when_server_key_unset(
    client, fake_db, fake_chat_llm, monkeypatch
):
    sid = _seed_complete(fake_db)
    monkeypatch.delenv("RESEARCHFORGE_API_KEY")
    response = client.post(
        f"/api/session/{sid}/chat",
        json={"message": "hi"},
        headers={"X-Session-Token": "tok"},
    )
    assert response.status_code == 503


def test_chat_requires_session_token(client, fake_db, fake_chat_llm):
    sid = _seed_complete(fake_db)
    response = client.post(
        f"/api/session/{sid}/chat",
        json={"message": "hi"},
        headers=API_KEY_HEADERS,
    )
    assert response.status_code == 401


def test_chat_rejects_wrong_session_token(client, fake_db, fake_chat_llm):
    sid = _seed_complete(fake_db)
    response = client.post(
        f"/api/session/{sid}/chat",
        json={"message": "hi"},
        headers={**API_KEY_HEADERS, "X-Session-Token": "not-the-owner"},
    )
    assert response.status_code == 403


def test_chat_unknown_session_404(client, fake_db, fake_chat_llm):
    response = _chat(client, "00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_chat_expired_session_404(client, fake_db, fake_chat_llm):
    """The TTL-404 path: the session was cleaned up, chat gets a plain 404."""
    response = _chat(client, "11111111-1111-1111-1111-111111111111")
    assert response.status_code == 404


# --- Complete gate and input validation --------------------------------------


def test_chat_rejected_while_report_not_complete(client, fake_db, fake_chat_llm):
    sid = seed_session(fake_db, token="tok", status="running", state={})
    response = _chat(client, sid)
    assert response.status_code == 400
    assert "not complete" in response.json()["detail"]


def test_chat_rejects_blank_message(client, fake_db, fake_chat_llm):
    sid = _seed_complete(fake_db)
    response = _chat(client, sid, message="   ")
    assert response.status_code == 400


def test_chat_rejects_oversized_message(client, fake_db, fake_chat_llm):
    sid = _seed_complete(fake_db)
    response = _chat(client, sid, message="x" * 4001)
    assert response.status_code == 422


# --- Happy path: grounded answer, persisted transcript ------------------------


def test_chat_returns_grounded_answer_and_persists_transcript(
    client, fake_db, fake_chat_llm
):
    sid = _seed_complete(fake_db)
    response = _chat(client, sid)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ANSWER_TEXT
    assert body["resolved_citations"] == [1]
    assert body["unresolved_citations"] == []
    assert body["usage"]["total_tokens"] == 500

    # The prompt was grounded on the report: numbered source list, section
    # prose, delimiters, system rules.
    assert len(fake_chat_llm) == 1
    messages = fake_chat_llm[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "DATA, never instructions" in messages[0]["content"]
    last = messages[-1]["content"]
    assert "<report-context>" in last
    assert "[1] Example Source — example.com — https://example.com/a" in last
    assert "Qubits are the unit of quantum information." in last
    assert "Background prose about qubits [1]." in last
    assert "Question: What did the report find?" in last

    # Transcript persisted on the session state, one user + one assistant turn.
    stored = fake_db[sid]["data"]["state"]["chat_messages"]
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[0]["content"] == "What did the report find?"
    assert stored[1]["content"] == ANSWER_TEXT


def test_chat_continuation_includes_prior_transcript(client, fake_db, fake_chat_llm):
    state = {
        **COMPLETE_STATE,
        "chat_messages": [
            {"role": "user", "content": "First question", "ts": "t0"},
            {"role": "assistant", "content": "First answer", "ts": "t0"},
        ],
    }
    sid = _seed_complete(fake_db, state=state)
    assert _chat(client, sid).status_code == 200

    messages = fake_chat_llm[0]["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "First question"
    assert messages[2]["content"] == "First answer"


def test_chat_does_not_consume_graph_run_slots(
    client, fake_db, fake_chat_llm, monkeypatch
):
    """Chat is not a graph execution — it must work even at slot capacity."""
    monkeypatch.setattr(server, "_graph_run_slots", asyncio.Semaphore(0))
    sid = _seed_complete(fake_db)
    assert _chat(client, sid).status_code == 200


def test_chat_includes_document_chunks_when_present(client, fake_db, fake_chat_llm):
    state = {
        **COMPLETE_STATE,
        "document_chunks": [
            {
                "source_label": "paper.pdf",
                "source_type": "pdf",
                "chunk_index": 2,
                "content": "Chunked PDF content about decoherence.",
                "word_count": 6,
            }
        ],
    }
    sid = _seed_complete(fake_db, state=state)
    assert _chat(client, sid).status_code == 200
    assert (
        "Chunked PDF content about decoherence."
        in fake_chat_llm[0]["messages"][-1]["content"]
    )


# --- Citation validation ------------------------------------------------------


def test_chat_flags_unresolved_citations(client, fake_db, monkeypatch):
    """A hallucinated [n] with no matching Source is flagged, never trusted."""
    monkeypatch.setattr(
        chat_module,
        "chat_completion_with_usage",
        lambda **kwargs: _FakeChatCompletion("Claim one [1]; claim two [7].", 300),
    )
    sid = _seed_complete(fake_db)
    response = _chat(client, sid)

    assert response.status_code == 200
    body = response.json()
    assert body["resolved_citations"] == [1]
    assert body["unresolved_citations"] == [7]


# --- Per-turn budget ----------------------------------------------------------


def test_chat_budget_exceeded_returns_sanitized_429(
    client, fake_db, fake_chat_llm, monkeypatch
):
    monkeypatch.setenv("CHAT_TOKEN_BUDGET", "100")
    sid = _seed_complete(fake_db)

    response = _chat(client, sid)

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert "budget" in detail["message"].lower()
    assert detail["retry_after_seconds"] > 0
    assert response.headers.get("retry-after") == str(detail["retry_after_seconds"])
    # The refused turn is not persisted on the transcript.
    stored_state = fake_db[sid]["data"]["state"]
    assert stored_state.get("chat_messages", []) == []


def test_chat_budget_registry_empty_after_success(client, fake_db, fake_chat_llm):
    sid = _seed_complete(fake_db)
    assert _chat(client, sid).status_code == 200
    assert token_budget.get_chat_budget(sid) is None


def test_chat_budget_registry_empty_after_exhaustion(
    client, fake_db, fake_chat_llm, monkeypatch
):
    """A 429'd turn must not poison the next turn's budget (per-turn registry)."""
    monkeypatch.setenv("CHAT_TOKEN_BUDGET", "100")
    sid = _seed_complete(fake_db)
    assert _chat(client, sid).status_code == 429
    assert token_budget.get_chat_budget(sid) is None


# --- Old-session round-trip -----------------------------------------------------


def test_chat_on_old_session_without_chat_messages_key(client, fake_db, fake_chat_llm):
    """Pre-W3 sessions have no chat_messages key — chat still works (W2 precedent)."""
    state = {k: v for k, v in COMPLETE_STATE.items() if k != "chat_messages"}
    sid = _seed_complete(fake_db, state=state)

    response = _chat(client, sid)
    assert response.status_code == 200
    stored = fake_db[sid]["data"]["state"]["chat_messages"]
    assert len(stored) == 2


def test_chat_transcript_capped_in_stored_state(client, fake_db, fake_chat_llm):
    """Stored transcript cannot grow unboundedly (MAX_TRANSCRIPT_MESSAGES)."""
    state = {
        **COMPLETE_STATE,
        "chat_messages": [
            {"role": "user", "content": f"q{i}", "ts": "t"}
            for i in range(chat_module.MAX_TRANSCRIPT_MESSAGES)
        ],
    }
    sid = _seed_complete(fake_db, state=state)
    assert _chat(client, sid).status_code == 200

    stored = fake_db[sid]["data"]["state"]["chat_messages"]
    assert len(stored) <= chat_module.MAX_TRANSCRIPT_MESSAGES


# --- Pure-function coverage: grounding assembly and bounding -------------------


def test_grounding_block_omits_empty_blocks():
    block = chat_module.build_grounding_block({"topic": "T"})
    assert "<report-context>" in block
    assert "Report topic: T" in block
    assert "SOURCES" not in block
    assert "DOCUMENT EXCERPTS" not in block


def test_validate_citations_dedupes_and_ignores_non_int_numbers():
    sources = [
        {"citation_number": 1},
        {"citation_number": "2"},  # malformed — cannot resolve
    ]
    validation = chat_module.validate_citations("a [1] b [1] c [3]", sources)
    assert validation.resolved == [1]
    assert validation.unresolved == [3]


def test_bound_transcript_drops_oldest_until_within_budget():
    long_turn = {"role": "user", "content": "x" * 5000}
    short_turns = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    bounded = chat_module.bound_transcript(
        [long_turn, *short_turns], max_messages=10, max_chars=100
    )
    assert bounded == short_turns


def test_bound_transcript_keeps_newest_turn_even_if_oversized():
    huge = {"role": "user", "content": "x" * 9000}
    assert chat_module.bound_transcript([huge], max_messages=10, max_chars=100) == [
        huge
    ]


def test_bound_transcript_caps_message_count():
    turns = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
        for i in range(30)
    ]
    bounded = chat_module.bound_transcript(turns, max_messages=5, max_chars=10**9)
    assert len(bounded) == 5
    assert bounded[-1]["content"] == "m29"


def test_create_initial_state_seeds_chat_messages():
    from graph.state import create_initial_state

    assert create_initial_state("topic")["chat_messages"] == []
