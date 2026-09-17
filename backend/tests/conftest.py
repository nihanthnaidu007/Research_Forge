"""Shared fixtures for the ResearchForge backend test suite.

The suite never touches a real database, OpenAI, or Tavily:
- the db layer is replaced with an in-memory store (fake_db fixture),
- the LangGraph pipeline is replaced with a scripted FakeGraph,
- external client factories raise if invoked.

server.py is imported after the test environment is configured because it
reads some variables (CORS_ORIGINS, MAX_CONCURRENT_RUNS) at import time.
"""

import copy
import os
import sys
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Configure environment BEFORE importing server.py (import-time reads).
TEST_API_KEY = "test-api-key-12345"
for _var, _value in {
    "OPENAI_API_KEY": "test-openai-key",
    "TAVILY_API_KEY": "test-tavily-key",
    "DATABASE_URL": "postgresql://test:test@localhost:5432/test",
    "CORS_ORIGINS": "http://localhost:3000",
    "RESEARCHFORGE_API_KEY": TEST_API_KEY,
    "MAX_CONCURRENT_RUNS": "3",
    "RUN_TOKEN_BUDGET": "",
}.items():
    os.environ[_var] = _value

import server  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import auth  # noqa: E402

API_KEY_HEADERS = {"X-API-Key": TEST_API_KEY}


class FakeGraph:
    """Scripted stand-in for the compiled LangGraph.

    invoke() returns scripted states in order (defaulting to an immediate
    END state) and records every invocation/update so tests can assert on
    the orchestration contract.
    """

    def __init__(self, scripted_results=None):
        self.scripted_results = list(scripted_results or [])
        self.invocations = []
        self.state_updates = []

    def invoke(self, state, config):
        self.invocations.append({"state": copy.deepcopy(state), "config": config})
        if self.scripted_results:
            return self.scripted_results.pop(0)
        base = state if isinstance(state, dict) else {}
        return {
            **base,
            "next_agent": "END",
            "is_complete": True,
            "stream_updates": base.get("stream_updates", []),
        }

    def update_state(self, config, updates):
        self.state_updates.append(copy.deepcopy(updates))


@pytest.fixture(autouse=True)
def test_environment(monkeypatch):
    """Deterministic env for every test (mirrors the import-time values)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("RESEARCHFORGE_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "")
    yield


@pytest.fixture(autouse=True)
def no_external_clients(monkeypatch):
    """Fail loudly if any test tries to reach OpenAI or Tavily."""
    import utils.clients as clients

    def _forbidden(name):
        def _raise(*args, **kwargs):
            raise AssertionError(
                f"{name} must not be called in tests — external providers are mocked"
            )

        return _raise

    monkeypatch.setattr(clients, "get_openai_client", _forbidden("get_openai_client"))
    monkeypatch.setattr(clients, "get_tavily_client", _forbidden("get_tavily_client"))
    yield


@pytest.fixture(autouse=True)
def disable_rate_limiter():
    """Rate limits are per-IP in-memory; disable for deterministic tests."""
    server.limiter.enabled = False
    yield
    server.limiter.enabled = True


@pytest.fixture
def fake_db(monkeypatch):
    """In-memory replacement for the db layer, hooked into server and auth.

    Mirrors the real db semantics: get_session returns a fresh copy each
    call (mutation is only persisted via update_session), and the token
    hash lives outside the client-visible session dict.
    """
    store = {}

    def create_session(session_id, data, session_token_hash=None):
        store[session_id] = {
            "data": copy.deepcopy(data),
            "token_hash": session_token_hash,
        }

    def get_session(session_id):
        row = store.get(session_id)
        return copy.deepcopy(row["data"]) if row else None

    def update_session(session_id, updates):
        row = store.get(session_id)
        if row is None:
            return
        allowed = {"status", "run_name", "trace_url", "versioning_report", "state"}
        for key, value in updates.items():
            if key in allowed:
                row["data"][key] = copy.deepcopy(value)

    def get_session_token_hash(session_id):
        row = store.get(session_id)
        return row["token_hash"] if row else None

    def list_sessions(limit=50, offset=0):
        # Mirrors db.list_sessions: metadata only, newest first.
        rows = sorted(
            (copy.deepcopy(row["data"]) for row in store.values()),
            key=lambda row: row.get("created_at", ""),
            reverse=True,
        )
        return [
            {
                "id": row["id"],
                "topic": row["topic"],
                "depth": row["depth"],
                "status": row["status"],
                "created_at": row.get("created_at", ""),
                "updated_at": row.get("updated_at", row.get("created_at", "")),
            }
            for row in rows[offset : offset + limit]
        ]

    monkeypatch.setattr(server, "create_session", create_session)
    monkeypatch.setattr(server, "get_session", get_session)
    monkeypatch.setattr(server, "update_session", update_session)
    monkeypatch.setattr(server, "list_sessions", list_sessions)
    monkeypatch.setattr(server, "db_cleanup_sessions", lambda ttl: [])
    monkeypatch.setattr(auth, "get_session_token_hash", get_session_token_hash)
    return store


def seed_session(store, token=None, status="waiting_approval", state=None):
    """Insert a session into the fake store; returns the session id."""
    session_id = str(uuid.uuid4())
    store[session_id] = {
        "data": {
            "id": session_id,
            "topic": "Seeded session",
            "depth": "quick",
            "status": status,
            "run_name": None,
            "trace_url": None,
            "state": state if state is not None else {"stream_updates": []},
            "created_at": "2026-09-17T00:00:00+00:00",
        },
        "token_hash": auth.hash_session_token(token) if token else None,
    }
    return session_id


@pytest.fixture
def client():
    # No context manager on purpose: lifespan (real DB + checkpointer) must not run.
    return TestClient(server.app)
