"""Tests for the W5 report-template presets.

The presets parameterize ONLY the outline system prompt. Two couplings are
guarded here because they are the spec's locked decisions:

1. num_sections keys on depth alone — a preset may never change the
   section count or the truncation cap.
2. synthesis is fixed — presets make no section-length promises
   (SYNTHESIS_SYSTEM_PROMPT's 180-250 words/section stays the only cap).

Plus the not-PRISMA wording guard for systematic-lite: the preset says
what it does, never borrowing systematic-review vocabulary.
"""

import json
import re

import pytest
import server
from conftest import API_KEY_HEADERS
from pydantic import ValidationError

import graph.agents.outline as outline_module
from graph.agents.outline import OUTLINE_SYSTEM_PROMPT, generate_outline, outline_node
from graph.agents.templates import (
    DEFAULT_TEMPLATE,
    PRESETS,
    TEMPLATE_PATTERN,
    build_outline_messages,
    template_rules,
)
from graph.state import create_initial_state

# --- Registry invariants ----------------------------------------------------------


def test_registry_keys_are_exactly_the_five_presets():
    assert set(PRESETS) == {
        "standard",
        "exec-brief",
        "journal-club",
        "deep-dive",
        "systematic-lite",
    }
    assert DEFAULT_TEMPLATE == "standard"


def test_every_preset_has_label_description_and_rules():
    for key, preset in PRESETS.items():
        assert preset["label"], f"{key} missing label"
        assert preset["description"], f"{key} missing description"
        assert isinstance(preset["rules"], str)


def test_template_pattern_built_from_registry():
    # Server validation and the registry can never drift.
    pattern = re.compile(TEMPLATE_PATTERN)
    for key in PRESETS:
        assert pattern.fullmatch(key)
    assert not pattern.fullmatch("bogus")


def test_systematic_lite_never_borrows_review_vocabulary():
    # Not-PRISMA wording surface: describe what the preset does; the
    # banned phrases are the systematic-review method vocabulary.
    banned = re.compile(
        r"screening|inclusion criteria|exclusion criteria|prisma", re.IGNORECASE
    )
    for key, preset in PRESETS.items():
        corpus = f"{preset['label']} {preset['description']} {preset['rules']}"
        assert not banned.search(corpus), f"{key} borrows review vocabulary"


def test_presets_make_no_section_length_promises():
    # Synthesis coupling: templates shape outline structure only. Any
    # "words" promise in the rules would drift against the fixed
    # 180-250-word synthesis cap.
    for key, preset in PRESETS.items():
        assert "words" not in preset["rules"].lower(), f"{key} promises section length"


# --- Prompt shape per preset (the extracted pure builder) -------------------------


def _messages_for(template):
    return build_outline_messages(
        topic="Quantum error correction",
        num_sections=3,
        template=template,
        research_results=[{"title": "A result", "snippet": "It works"}],
        fact_check_results=[{"verdict": "TRUE", "claim": "It works"}],
        document_summary="",
    )


def test_standard_preset_appends_nothing():
    messages = _messages_for("standard")
    assert len(messages) == 2
    assert messages[0] == {"role": "system", "content": OUTLINE_SYSTEM_PROMPT}


@pytest.mark.parametrize(
    "template",
    ["exec-brief", "journal-club", "deep-dive", "systematic-lite"],
)
def test_named_presets_extend_the_base_prompt(template):
    system = _messages_for(template)[0]["content"]
    # Base rules survive; preset rules are appended.
    assert system.startswith(OUTLINE_SYSTEM_PROMPT)
    assert template_rules(template) in system
    # And each preset actually changes the prompt.
    assert system != OUTLINE_SYSTEM_PROMPT


def test_presets_produce_distinct_system_prompts():
    systems = {_messages_for(t)[0]["content"] for t in PRESETS}
    assert len(systems) == len(PRESETS)


def test_unknown_template_falls_back_to_standard_rules():
    # Defensive: unknown template ids (e.g. a restored checkpoint carrying
    # garbage) render the standard prompt instead of crashing.
    assert template_rules("nonexistent") == template_rules("standard")


# --- Coupling guard 1: num_sections keys on depth alone ---------------------------


def test_user_prompt_identical_across_presets_at_same_count():
    user_prompts = {_messages_for(t)[1]["content"] for t in PRESETS}
    assert len(user_prompts) == 1
    assert "Number of sections to generate: 3" in user_prompts.pop()


class _FakeResponse:
    def __init__(self, sections):
        message = type("Message", (), {"content": json.dumps({"sections": sections})})()
        self.choices = [type("Choice", (), {"message": message})()]


def _capture_generate_outline(monkeypatch, sections):
    captured = {}

    def _fake_llm(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _FakeResponse(sections)

    monkeypatch.setattr(outline_module, "chat_completion_with_usage", _fake_llm)
    return captured


def test_generate_outline_keeps_section_count_depth_only(monkeypatch):
    sections = [
        {"title": f"Section {i}", "description": "d", "section_id": f"sec_{i}", "order": i}
        for i in range(1, 9)
    ]
    captured = _capture_generate_outline(monkeypatch, sections)

    for template in PRESETS:
        captured.clear()
        outline = generate_outline("topic", "quick", [], [], template=template)
        # Truncation cap is untouched: the LLM offered 8, depth=quick pins 3.
        assert len(outline) == 3, template
        user_prompt = captured["messages"][1]["content"]
        assert "Number of sections to generate: 3" in user_prompt, template
        assert "Generate a 3-section report outline" in user_prompt, template


def test_generate_outline_sends_single_system_and_user_message(monkeypatch):
    captured = _capture_generate_outline(
        monkeypatch,
        [{"title": "T", "description": "d", "section_id": "sec_1", "order": 1}],
    )
    generate_outline("topic", "quick", [], [], template="exec-brief")
    roles = [m["role"] for m in captured["messages"]]
    assert roles == ["system", "user"]


# --- State and session plumbing -----------------------------------------------------


def test_create_initial_state_defaults_to_standard():
    initial = create_initial_state("topic")
    assert initial["report_template"] == "standard"


def test_create_initial_state_carries_explicit_template():
    initial = create_initial_state("topic", report_template="deep-dive")
    assert initial["report_template"] == "deep-dive"


def test_run_report_request_rejects_unknown_template():
    with pytest.raises(ValidationError):
        server.RunReportRequest(topic="Valid topic", template="bogus")


def test_run_report_request_defaults_template():
    request = server.RunReportRequest(topic="Valid topic")
    assert request.template == "standard"


class _EndGraph:
    def invoke(self, state, config):
        return {**state, "next_agent": "END", "is_complete": True}

    def update_state(self, config, updates):
        pass


def test_run_persists_template_into_session_state(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "get_graph", lambda: _EndGraph())

    run = client.post(
        "/api/run",
        json={"topic": "Template plumbing topic", "template": "exec-brief"},
        headers=API_KEY_HEADERS,
    )
    assert run.status_code == 200, run.text
    sid = run.json()["session_id"]
    assert fake_db[sid]["data"]["state"]["report_template"] == "exec-brief"


def test_run_accepts_missing_template(client, fake_db, monkeypatch):
    monkeypatch.setattr(server, "get_graph", lambda: _EndGraph())

    run = client.post(
        "/api/run",
        json={"topic": "Default template topic"},
        headers=API_KEY_HEADERS,
    )
    assert run.status_code == 200, run.text
    sid = run.json()["session_id"]
    assert fake_db[sid]["data"]["state"]["report_template"] == "standard"


def test_run_rejects_unknown_template_with_422(client, fake_db):
    response = client.post(
        "/api/run",
        json={"topic": "Valid topic", "template": "bogus"},
        headers=API_KEY_HEADERS,
    )
    assert response.status_code == 422


# --- outline_node threads the template ---------------------------------------------


def _state_with(template=None):
    state = {
        "topic": "topic",
        "depth": "quick",
        "fact_check_results": [],
        "research_results": [],
        "document_summary": "",
        "stream_updates": [],
        "completed_agents": [],
    }
    if template is not None:
        state["report_template"] = template
    return state


def test_outline_node_reads_template_from_state(monkeypatch):
    seen = {}

    def _fake_generate(topic, depth, fact_checks, research, doc_summary, template):
        seen["template"] = template
        return [
            {"section_id": "sec_1", "title": "T", "description": "d", "order": 1}
        ]

    monkeypatch.setattr(outline_module, "generate_outline", _fake_generate)

    state = _state_with("journal-club")
    outline_node(state)
    assert seen["template"] == "journal-club"
    # The stream update names the template for the operator.
    assert any("journal-club template" in u for u in state["stream_updates"])


def test_outline_node_defaults_template_on_pre_w5_state(monkeypatch):
    # Restored pre-W5 checkpoints carry no report_template key (or a None
    # value) — the node must fall back to standard, never crash.
    seen = {}

    def _fake_generate(topic, depth, fact_checks, research, doc_summary, template):
        seen["template"] = template
        return [
            {"section_id": "sec_1", "title": "T", "description": "d", "order": 1}
        ]

    monkeypatch.setattr(outline_module, "generate_outline", _fake_generate)

    state = _state_with()
    outline_node(state)
    assert seen["template"] == "standard"

    state = _state_with(None)
    outline_node(state)
    assert seen["template"] == "standard"
