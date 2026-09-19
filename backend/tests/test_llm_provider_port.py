"""D1–D3 LLM provider port: Anthropic behind the shared OpenAI-shaped wrapper,
optional Tavily with a run-visible skip note, and run-start provider preflight.

No test in this file touches the network: the LLM client is a scripted fake
whose responses carry Anthropic-shaped usage (input_tokens/output_tokens, no
total_tokens), and client factories are patched per test. The "zero OpenAI
calls" guarantee is enforced by replacing the openai.OpenAI constructor in the
clients module with an assertion-raise.
"""

import json
from types import SimpleNamespace

import pytest
import server
from conftest import API_KEY_HEADERS

from graph import graph as graph_module
from graph.agents import citations as citations_module
from graph.agents import research as research_module
from graph.agents.factcheck import extract_claims_from_research
from graph.agents.outline import generate_outline
from graph.state import create_initial_state
from utils import clients, preflight, token_budget
from utils.llm_utils import extract_json_object

# Captured at collection time, BEFORE conftest's no_external_clients fixture
# swaps the module attribute for a raising stub. Used only by the
# client-construction tests, which exercise the real singleton builder.
_REAL_GET_OPENAI_CLIENT = clients.get_openai_client


# --- scripted Anthropic-shaped client -------------------------------------


class _AnthropicUsage:
    """Anthropic-native usage shape — deliberately has NO total_tokens."""

    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _StatusError(Exception):
    """Carries an HTTP status code the way OpenAI SDK errors do."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _fake_response(content, usage):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage,
    )


class _FakeCompletions:
    def __init__(self, responder):
        self._responder = responder
        self.calls = 0
        self.kwargs_log = []

    def create(self, **kwargs):
        self.calls += 1
        self.kwargs_log.append(kwargs)
        return self._responder(kwargs)


class _FakeClient:
    def __init__(self, completions):
        self.chat = SimpleNamespace(completions=completions)


def _completions_responder(kwargs):
    """Dispatch on the full prompt text — mirrors the real per-agent prompts."""
    text = " ".join(str(m.get("content", "")) for m in kwargs["messages"])
    if "claim extraction specialist" in text:
        content = json.dumps(
            {"claims": ["Claim A about the topic", "Claim B about the topic"]}
        )
    elif "fact-checking AI" in text:
        content = json.dumps(
            {
                "verdict": "SUPPORTED",
                "confidence": 0.9,
                "reasoning": "Sources support the claim.",
                "supporting_urls": ["https://example.com/a"],
            }
        )
    elif "research report architect" in text:
        content = json.dumps(
            {
                "sections": [
                    {
                        "section_id": "sec_1",
                        "title": "Background",
                        "description": "Foundational context and prior work.",
                        "order": 1,
                    },
                    {
                        "section_id": "sec_2",
                        "title": "Current State",
                        "description": "Where the field stands today.",
                        "order": 2,
                    },
                    {
                        "section_id": "sec_3",
                        "title": "Outlook",
                        "description": "What comes next and why it matters.",
                        "order": 3,
                    },
                ]
            }
        )
    else:
        content = (
            "Synthesized section prose grounded in the provided sources "
            "[source: https://example.com/a] [source: https://arxiv.org/abs/1234]."
        )
    return _fake_response(content, _AnthropicUsage(10, 5))


_SCHOLARLY_RESULTS = (
    [
        {
            "url": "https://example.com/a",
            "title": "Semantic Scholar result",
            "snippet": "Findings about the topic from Semantic Scholar.",
            "source_domain": "example.com",
            "relevance_score": 0.9,
        },
        {
            "url": "https://arxiv.org/abs/1234",
            "title": "arXiv result",
            "snippet": "Findings about the topic from arXiv.",
            "source_domain": "arxiv.org",
            "relevance_score": 0.8,
        },
    ],
    {"semantic_scholar": 2},
)


@pytest.fixture(autouse=True)
def _reset_preflight_cache():
    preflight.reset_preflight_cache()
    yield
    preflight.reset_preflight_cache()


def _forbid_real_openai(monkeypatch):
    """Fail loudly if anything constructs a real OpenAI client."""

    def _no_openai(*args, **kwargs):
        raise AssertionError("Real OpenAI client constructed — provider leak")

    monkeypatch.setattr(clients, "OpenAI", _no_openai)


# --- D1: provider + model selection ---------------------------------------


def test_llm_provider_defaults_to_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert clients.llm_provider() == "anthropic"


def test_llm_model_provider_aware_defaults(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert clients.llm_model() == "claude-haiku-4-5-20251001"
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert clients.llm_model() == "gpt-4o"
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-whatever")
    assert clients.llm_model() == "claude-sonnet-whatever"


def test_get_openai_client_anthropic_points_at_compat_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(clients, "get_openai_client", _REAL_GET_OPENAI_CLIENT)
    monkeypatch.setattr(clients, "_openai_client", None)
    constructed = {}

    class _FakeSDKClient:
        def __init__(self, api_key=None, base_url=None):
            constructed["api_key"] = api_key
            constructed["base_url"] = base_url

    monkeypatch.setattr(clients, "OpenAI", _FakeSDKClient)
    monkeypatch.setattr(clients, "_openai_client", None)

    client = clients.get_openai_client()

    assert isinstance(client, _FakeSDKClient)
    assert constructed["base_url"] == clients.ANTHROPIC_OPENAI_BASE_URL
    assert constructed["api_key"] == "sk-ant-test"


def test_get_openai_client_openai_path_preserved(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setattr(clients, "get_openai_client", _REAL_GET_OPENAI_CLIENT)
    monkeypatch.setattr(clients, "_openai_client", None)
    constructed = {}

    class _FakeSDKClient:
        def __init__(self, api_key=None, base_url=None):
            constructed["api_key"] = api_key
            constructed["base_url"] = base_url

    monkeypatch.setattr(clients, "OpenAI", _FakeSDKClient)
    monkeypatch.setattr(clients, "_openai_client", None)

    clients.get_openai_client()

    assert constructed["base_url"] is None
    assert constructed["api_key"] == "sk-openai-test"


def test_get_openai_client_missing_provider_key_raises_clearly(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(clients, "get_openai_client", _REAL_GET_OPENAI_CLIENT)
    monkeypatch.setattr(clients, "_openai_client", None)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        clients.get_openai_client()


# --- D1: usage accounting across usage shapes -----------------------------


@pytest.mark.parametrize(
    "usage, expected",
    [
        (None, None),
        (SimpleNamespace(total_tokens=42), 42),
        (SimpleNamespace(prompt_tokens=30, completion_tokens=12), 42),
        (SimpleNamespace(input_tokens=30, output_tokens=12), 42),
        (SimpleNamespace(unrelated="x"), None),
    ],
)
def test_usage_total_tokens_normalizes_shapes(usage, expected):
    assert clients.usage_total_tokens(usage) == expected


def test_chat_completion_records_anthropic_shaped_usage(monkeypatch):
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "1000")
    completions = _FakeCompletions(
        lambda kwargs: _fake_response("ok", _AnthropicUsage(30, 12))
    )
    monkeypatch.setattr(clients, "get_openai_client", lambda: _FakeClient(completions))

    budget = token_budget.ensure_budget("sess-anthropic-usage")
    ctx = token_budget.set_session_context("sess-anthropic-usage")
    try:
        clients.chat_completion_with_usage(
            model="claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert budget.used_tokens == 42
    finally:
        token_budget.reset_session_context(ctx)
        token_budget.release_budget("sess-anthropic-usage")


# --- D1: fence-tolerant JSON (compat layer ignores response_format) -------


def test_extract_json_object_accepts_raw_json():
    assert extract_json_object('{"claims": ["a"]}') == {"claims": ["a"]}


def test_extract_json_object_accepts_fenced_json():
    assert extract_json_object('```json\n{"claims": ["a"]}\n```') == {"claims": ["a"]}
    assert extract_json_object('```\n{"claims": ["a"]}\n```') == {"claims": ["a"]}


def test_extract_json_object_raises_without_json():
    with pytest.raises(ValueError):
        extract_json_object("no json here")


def test_outline_parses_fenced_json_from_anthropic(monkeypatch):
    fenced = '```json\n{"sections": [{"section_id": "sec_1", "title": "T", "description": "D", "order": 1}]}\n```'
    completions = _FakeCompletions(lambda kwargs: _fake_response(fenced, _AnthropicUsage(10, 5)))
    monkeypatch.setattr(clients, "get_openai_client", lambda: _FakeClient(completions))

    sections = generate_outline(
        topic="Topic",
        depth="quick",
        fact_check_results=[],
        research_results=[],
    )

    assert len(sections) == 1
    assert sections[0]["title"] == "T"


def test_extract_claims_falls_back_on_garbage_json(monkeypatch):
    completions = _FakeCompletions(
        lambda kwargs: _fake_response("totally not json", _AnthropicUsage(10, 5))
    )
    monkeypatch.setattr(clients, "get_openai_client", lambda: _FakeClient(completions))

    claims = extract_claims_from_research(
        [{"source_domain": "example.com", "snippet": "x" * 60}]
    )

    assert claims == ["x" * 60 + "..."]  # snippet-based fallback claims


# --- D1: full report graph on the mocked Anthropic-shaped client ----------


def test_full_report_graph_completes_on_anthropic_shaped_client(monkeypatch):
    monkeypatch.setenv("RUN_TOKEN_BUDGET", "10000")
    _forbid_real_openai(monkeypatch)
    completions = _FakeCompletions(_completions_responder)
    monkeypatch.setattr(clients, "get_openai_client", lambda: _FakeClient(completions))

    # Research leg: Tavily absent, scholarly retrieval returns fixtures.
    monkeypatch.setattr(research_module, "get_tavily_client", lambda: None)
    monkeypatch.setattr(
        research_module, "collect_scholarly_results", lambda queries: _SCHOLARLY_RESULTS
    )
    # Citations resolve integrity against S2/Crossref — degrade to "unknown"
    # so the graph test makes zero network calls of any kind.
    monkeypatch.setattr(
        citations_module,
        "enrich_citations_with_integrity",
        lambda sources: [{**s, "integrity_status": "unknown"} for s in sources],
    )

    from langgraph.checkpoint.memory import MemorySaver

    monkeypatch.setattr(graph_module, "get_checkpointer", lambda: MemorySaver())
    monkeypatch.setattr(graph_module, "_compiled_graph", None)

    graph = graph_module.get_graph()
    state = create_initial_state(topic="Quantum computing", depth="quick")
    config = {"configurable": {"thread_id": "test-llm-port"}}

    budget = token_budget.ensure_budget("sess-llm-port-graph")
    ctx = token_budget.set_session_context("sess-llm-port-graph")
    try:
        paused = graph.invoke(state, config)
        assert paused.get("next_agent") == "synthesis"
        assert paused.get("outline_approved") is False

        graph.update_state(
            config,
            {
                "outline_approved": True,
                "approved_outline": paused["outline"],
                "current_section_index": 0,
            },
        )

        # interrupt_before=["synthesis"] fires for EVERY section write (one
        # pause per section) — mirror the server's
        # _drive_post_approval_iterations: loop invoke(None) until the graph
        # reaches END/error, with a bounded iteration guard.
        final = paused
        for _ in range(24):
            snapshot = graph.get_state(config)
            if not snapshot.next or final.get("is_complete") or final.get("error"):
                break
            final = graph.invoke(None, config)

        assert not final.get("error"), final.get("error")
        assert final.get("is_complete") is True
        assert len(final.get("written_sections", [])) == 3
        assert final.get("sources"), "citations must list sources"
        # extract claims + 2 judges + outline + 3 sections — all through the fake
        assert completions.calls >= 6
        # usage recorded per call from anthropic-shaped input/output tokens
        assert budget.used_tokens == 15 * completions.calls
    finally:
        token_budget.reset_session_context(ctx)
        token_budget.release_budget("sess-llm-port-graph")


# --- D2: Tavily optional with run-visible skip note ------------------------


def test_research_node_skips_web_search_without_tavily(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(research_module, "get_tavily_client", lambda: None)

    def _forbidden_search(*args, **kwargs):
        raise AssertionError("perform_tavily_search must not run without Tavily")

    monkeypatch.setattr(research_module, "perform_tavily_search", _forbidden_search)
    monkeypatch.setattr(
        research_module, "collect_scholarly_results", lambda queries: _SCHOLARLY_RESULTS
    )

    state = create_initial_state(topic="Quantum computing", depth="quick")
    result = research_module.research_node(state)

    assert result.get("error") is None
    assert "research" in result.get("completed_agents", [])
    assert len(result.get("research_results", [])) == 2  # scholarly-only sources
    assert not any("tavily" in (r.get("url") or "").lower() for r in result["research_results"])

    notes = [
        u
        for u in result.get("stream_updates", [])
        if "Web search unavailable" in u and "TAVILY_API_KEY" in u
    ]
    assert notes, "run-visible web-search skip note missing from stream"
    gaps = result.get("coverage_gaps") or []
    assert any("Web search unavailable" in g for g in gaps), (
        "coverage-gaps surface must reflect the missing web-search leg"
    )


def test_research_node_uses_web_search_when_tavily_present(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setattr(research_module, "get_tavily_client", lambda: SimpleNamespace())
    monkeypatch.setattr(
        research_module,
        "perform_tavily_search",
        lambda query, **kwargs: [
            {
                "url": "https://web.example/x",
                "title": "Web result",
                "snippet": "Web snippet.",
                "source_domain": "web.example",
                "relevance_score": 0.7,
            }
        ],
    )
    monkeypatch.setattr(
        research_module, "collect_scholarly_results", lambda queries: _SCHOLARLY_RESULTS
    )

    state = create_initial_state(topic="Quantum computing", depth="quick")
    result = research_module.research_node(state)

    assert result.get("error") is None
    assert any("web.example" in (r.get("url") or "") for r in result["research_results"])
    assert not result.get("coverage_gaps")


# --- D2/D1: validate_env_vars matrix ---------------------------------------


def _set_valid_base_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")


def test_validate_env_vars_anthropic_only_passes(monkeypatch):
    _set_valid_base_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    clients.validate_env_vars()  # must not raise


def test_validate_env_vars_openai_without_key_fails_clearly(monkeypatch):
    _set_valid_base_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc:
        clients.validate_env_vars()
    assert "OPENAI_API_KEY" in str(exc.value)


def test_validate_env_vars_anthropic_without_key_fails_clearly(monkeypatch):
    _set_valid_base_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError) as exc:
        clients.validate_env_vars()
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_validate_env_vars_invalid_provider_fails(monkeypatch):
    _set_valid_base_env(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")

    with pytest.raises(RuntimeError) as exc:
        clients.validate_env_vars()
    assert "LLM_PROVIDER" in str(exc.value)


# --- D3: preflight ----------------------------------------------------------


def test_preflight_missing_key_fails_fast_without_client(monkeypatch):
    monkeypatch.setenv("LLM_PREFLIGHT_TTL_SECONDS", "300")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _boom(*args, **kwargs):
        raise AssertionError("no client construction when the key is missing")

    monkeypatch.setattr(preflight, "get_openai_client", _boom)

    with pytest.raises(preflight.LLMProviderUnavailableError) as exc:
        preflight.verify_llm_provider()
    assert "LLM provider unavailable: ANTHROPIC_API_KEY" in str(exc.value)


def test_preflight_rejected_key_raises_typed_error(monkeypatch):
    monkeypatch.setenv("LLM_PREFLIGHT_TTL_SECONDS", "300")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "bad-key")

    def _reject(kwargs):
        raise _StatusError(401, "invalid x-api-key")

    monkeypatch.setattr(
        preflight,
        "get_openai_client",
        lambda: _FakeClient(_FakeCompletions(_reject)),
    )

    with pytest.raises(preflight.LLMProviderUnavailableError) as exc:
        preflight.verify_llm_provider(force=True)
    message = str(exc.value)
    assert "LLM provider unavailable: ANTHROPIC_API_KEY rejected the preflight call" in message
    assert "401" in message


def test_preflight_probe_is_minimal_and_uses_configured_model(monkeypatch):
    monkeypatch.setenv("LLM_PREFLIGHT_TTL_SECONDS", "300")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    captured = {}

    def _capture(kwargs):
        captured.update(kwargs)
        return _fake_response("ok", _AnthropicUsage(1, 1))

    monkeypatch.setattr(
        preflight,
        "get_openai_client",
        lambda: _FakeClient(_FakeCompletions(_capture)),
    )

    preflight.verify_llm_provider()

    assert captured["model"] == "claude-haiku-4-5-20251001"
    assert captured["max_completion_tokens"] == preflight.PREFLIGHT_PROBE_MAX_TOKENS


def test_preflight_success_is_cached_within_ttl(monkeypatch):
    monkeypatch.setenv("LLM_PREFLIGHT_TTL_SECONDS", "300")
    completions = _FakeCompletions(
        lambda kwargs: _fake_response("ok", _AnthropicUsage(1, 1))
    )
    monkeypatch.setattr(preflight, "get_openai_client", lambda: _FakeClient(completions))

    preflight.verify_llm_provider()
    preflight.verify_llm_provider()
    assert completions.calls == 1  # cached

    preflight.verify_llm_provider(force=True)
    assert completions.calls == 2


def test_preflight_ttl_zero_disables_cache(monkeypatch):
    monkeypatch.setenv("LLM_PREFLIGHT_TTL_SECONDS", "0")
    completions = _FakeCompletions(
        lambda kwargs: _fake_response("ok", _AnthropicUsage(1, 1))
    )
    monkeypatch.setattr(preflight, "get_openai_client", lambda: _FakeClient(completions))

    preflight.verify_llm_provider()
    preflight.verify_llm_provider()
    assert completions.calls == 2


# --- D3: run path fails fast with the typed error ---------------------------


def test_run_fails_fast_with_typed_preflight_error(client, fake_db, monkeypatch):
    monkeypatch.setattr(
        server,
        "verify_llm_provider",
        lambda: (_ for _ in ()).throw(
            preflight.LLMProviderUnavailableError(
                "LLM provider unavailable: ANTHROPIC_API_KEY is missing — "
                "set it in backend/.env (LLM_PROVIDER=anthropic)"
            )
        ),
    )

    response = client.post(
        "/api/run", json={"topic": "Quantum computing"}, headers=API_KEY_HEADERS
    )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    data = fake_db[session_id]["data"]
    assert data["status"] == "error"
    assert "LLM provider unavailable" in data["state"]["error"]
