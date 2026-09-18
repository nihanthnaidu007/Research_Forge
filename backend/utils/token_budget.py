"""
Per-run token budget accounting.

A registry mapping session_id -> RunTokenBudget plus a context var so LLM
call sites can attribute token usage to the run that triggered them (the
graph executes in worker threads via asyncio.to_thread, which copies the
caller's context, so a value set in the async task is visible inside it).

The budget is a cost ceiling, not a billing meter: when a run records more
tokens than RUN_TOKEN_BUDGET allows, record() raises TokenBudgetExceeded so
the graph execution fails fast with a sanitized client-facing error instead
of silently burning credits.

Budget semantics are per RUN (W4): the registry entry survives across graph
execution phases (initial run → waiting_approval → post-approval resume) and
across deep-research rounds, and is released only when the run truly ends —
completion or terminal error. The earlier per-phase semantics (full budget
re-armed each phase) silently multiplied the ceiling once re-research rounds
existed.

Each gap-driven research round is additionally bounded by its own sub-cap
(RESEARCH_ROUND_TOKEN_BUDGET) so a single round cannot consume the whole run
budget. Round budgets live in their own registry: the driver creates a fresh
one at each round start and releases it when the round ends (citations
reached) or the run terminates.
"""

import os
import threading
from contextvars import ContextVar, Token

_budgets: dict[str, "RunTokenBudget"] = {}
_chat_budgets: dict[str, "RunTokenBudget"] = {}
_round_budgets: dict[str, "RunTokenBudget"] = {}
_registry_lock = threading.Lock()

# Tracks which session the current call stack serves, so a shared OpenAI
# client can attribute usage across concurrent runs.
_current_session_id: ContextVar[str | None] = ContextVar(
    "current_session_id", default=None
)


class TokenBudgetExceeded(RuntimeError):
    """Raised when a run records more tokens than its budget allows."""


class RunTokenBudget:
    """Thread-safe token tally for a single run, with a hard ceiling."""

    def __init__(self, max_tokens: int | None):
        self.max_tokens = max_tokens
        self.used_tokens = 0
        self._lock = threading.Lock()

    def record(self, tokens: int) -> int:
        """
        Add tokens to the tally.

        Raises TokenBudgetExceeded when the ceiling is crossed, so the
        calling LLM call site aborts before spending further tokens.
        """
        if tokens <= 0:
            return self.used_tokens
        with self._lock:
            self.used_tokens += tokens
            if self.max_tokens is not None and self.used_tokens > self.max_tokens:
                raise TokenBudgetExceeded(
                    f"per-run token budget exhausted: "
                    f"{self.used_tokens} > {self.max_tokens} tokens"
                )
            return self.used_tokens


def _max_tokens_from_env_var(name: str) -> int | None:
    """Parse a token-budget env var; empty, zero, or invalid means unlimited."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _max_tokens_from_env() -> int | None:
    return _max_tokens_from_env_var("RUN_TOKEN_BUDGET")


def _chat_max_tokens_from_env() -> int | None:
    return _max_tokens_from_env_var("CHAT_TOKEN_BUDGET")


def ensure_budget(session_id: str) -> RunTokenBudget:
    """Return the session's budget, creating one from RUN_TOKEN_BUDGET if absent."""
    with _registry_lock:
        budget = _budgets.get(session_id)
        if budget is None:
            budget = RunTokenBudget(_max_tokens_from_env())
            _budgets[session_id] = budget
        return budget


def release_budget(session_id: str) -> None:
    """Drop the session's budget. Called when the run truly ends: completion
    or terminal error — never between phases or rounds (per-run semantics)."""
    with _registry_lock:
        _budgets.pop(session_id, None)


def get_budget(session_id: str) -> RunTokenBudget | None:
    with _registry_lock:
        return _budgets.get(session_id)


def _round_max_tokens_from_env() -> int | None:
    return _max_tokens_from_env_var("RESEARCH_ROUND_TOKEN_BUDGET")


def ensure_round_budget(session_id: str) -> RunTokenBudget:
    """
    Return the session's active research-round budget, creating one from
    RESEARCH_ROUND_TOKEN_BUDGET if absent.

    The driver calls this at each gap-tripped round start (after releasing
    the previous round's entry) so every round gets a fresh sub-cap. When
    the env var is unset the round budget is unlimited — the run budget
    remains the only ceiling.
    """
    with _registry_lock:
        budget = _round_budgets.get(session_id)
        if budget is None:
            budget = RunTokenBudget(_round_max_tokens_from_env())
            _round_budgets[session_id] = budget
        return budget


def release_round_budget(session_id: str) -> None:
    """Drop the session's round budget. Called when a round ends (citations
    reached) or the run terminates; a no-op when no round is active."""
    with _registry_lock:
        _round_budgets.pop(session_id, None)


def get_round_budget(session_id: str) -> RunTokenBudget | None:
    with _registry_lock:
        return _round_budgets.get(session_id)


def ensure_chat_budget(session_id: str) -> RunTokenBudget:
    """
    Return the session's per-turn chat budget, creating one from
    CHAT_TOKEN_BUDGET if absent.

    Chat turns live outside graph-execution phases, so they get their own
    registry: a chat turn must never share (or clobber) an active run-phase
    budget, and RUN_TOKEN_BUDGET stays untouched.
    """
    with _registry_lock:
        budget = _chat_budgets.get(session_id)
        if budget is None:
            budget = RunTokenBudget(_chat_max_tokens_from_env())
            _chat_budgets[session_id] = budget
        return budget


def release_chat_budget(session_id: str) -> None:
    """Drop the session's chat budget. Called when the chat turn ends."""
    with _registry_lock:
        _chat_budgets.pop(session_id, None)


def get_chat_budget(session_id: str) -> RunTokenBudget | None:
    with _registry_lock:
        return _chat_budgets.get(session_id)


def set_session_context(session_id: str) -> Token:
    """Bind LLM calls on this call stack (and its to_thread children) to a session."""
    return _current_session_id.set(session_id)


def reset_session_context(token: Token) -> None:
    _current_session_id.reset(token)


def record_usage_for_current_session(tokens: int) -> None:
    """
    Attribute tokens to the run active in the current context.

    No-op when no session context is set or the session has no registered
    budget; raises TokenBudgetExceeded when the active budget is exceeded —
    the run budget first, then the active round sub-cap when one is live.
    """
    session_id = _current_session_id.get()
    if session_id is None:
        return
    with _registry_lock:
        budget = _budgets.get(session_id)
        round_budget = _round_budgets.get(session_id)
        chat_budget = None
        if budget is None and round_budget is None:
            # Chat turns are gated to status == complete, when no run or
            # round budget exists for the session — chat only ever records
            # then (pre-W4 XOR semantics; CHAT_TOKEN_BUDGET untouched).
            chat_budget = _chat_budgets.get(session_id)
    if budget is not None:
        budget.record(tokens)
    if round_budget is not None:
        round_budget.record(tokens)
    if chat_budget is not None:
        chat_budget.record(tokens)
