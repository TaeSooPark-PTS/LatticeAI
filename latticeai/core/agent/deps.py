"""The ports the state machine needs from the outside world.

Everything the loop touches is declared here, which is what lets the whole
PLAN→EXECUTE→VERIFY cycle run against fakes: production wires these from the
app layer's router, governance map, audit log and prompts; tests pass an LLM
that returns canned JSON and a recording tool executor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, FrozenSet, Mapping, Optional

from latticeai.core.agent_helpers import PhaseBudgets, TranscriptBudget
from latticeai.core.agent_profiles import AgentProfile


@dataclass
class AgentDeps:
    """The ports a :class:`SingleAgentRuntime` needs from the outside world.

    Everything the state machine touches is here, so the loop can be exercised
    against fakes. See module docstring for the two-adapter rationale.
    """

    # ── LLM port ─────────────────────────────────────────────────────
    # generate_as(model_id, message, context, max_tokens, temperature) -> str
    generate_as: Callable[..., Awaitable[Any]]
    # generate(message, context, max_tokens, temperature) -> str
    generate: Callable[..., Awaitable[Any]]

    # ── tool port ────────────────────────────────────────────────────
    execute_tool: Callable[[str, dict], dict]
    policy_for: Callable[[str, dict], Mapping[str, Any]]   # name, args -> policy
    risk_level: Callable[[Any], str]               # policy -> "low"|"medium"|"high"
    check_role: Callable[[str, str], None]         # tool_name, user -> raises if not allowed
    tool_governance: Mapping[str, Mapping[str, Any]]  # name -> policy (auto_approve set)
    file_create_actions: FrozenSet[str]

    # ── context / memory / audit ports ───────────────────────────────
    recent_chat_context: Callable[..., str]        # (conversation_id=...) -> str
    clear_history: Callable[[int], dict]
    knowledge_save: Callable[..., Any]
    audit: Callable[..., None]                     # (event, **kw) -> None

    # ── prompts + config ─────────────────────────────────────────────
    planner_prompt: str
    executor_prompt: str
    critic_prompt: str
    memory_updater_prompt: str
    agent_root: Path

    # ── rollback port (optional) ─────────────────────────────────────
    # Production injects this from the tool dispatch service so this pure
    # state machine does not shell out directly. Tests can pass a recorder.
    rollback_file: Optional[Callable[[str], Dict[str, Any]]] = None

    # ── snapshot rollback ports (optional, review L7) ────────────────
    # git-only rollback left non-git workspaces and newly created files
    # unrecoverable. ``snapshot_file(path)`` captures pre-write state
    # ({"existed", "content", "too_large"}) before a file-create action;
    # ``restore_snapshot(path, content)`` restores it (content=None deletes
    # a file the run created). Both are production-wired with workspace
    # path safety; tests pass recorders.
    snapshot_file: Optional[Callable[[str], Dict[str, Any]]] = None
    restore_snapshot: Optional[Callable[[str, Optional[str]], Dict[str, Any]]] = None

    # ── lifecycle hooks port (optional) ──────────────────────────────
    # When present, every tool execution fires the shared pre_tool/post_tool
    # lifecycle, so the agent tool path no longer bypasses hooks.
    hooks: Any = None

    # ── brain memory port (optional) ─────────────────────────────────
    # When present, completed-run learnings become typed Experience records
    # through the unified ingestion pipeline (with provenance), replacing
    # the vault markdown dump.
    brain_memory: Any = None

    # ── change governor port (optional) ──────────────────────────────
    # When present, file writes are classified centrally: additive creates
    # run with minimal friction, while mutations/deletions of existing
    # content are staged as review proposals instead of applied. The port is
    # ``review(name, args, policy=..., user_email=..., workspace_id=...)``
    # returning None (fall through to the classic gates) or a verdict dict.
    change_governor: Any = None

    # ── phase budgets (optional) ─────────────────────────────────────
    # Per-phase token caps (plan/execute/verify/memory). None reads the
    # environment once at first use; tests inject a fixed PhaseBudgets.
    phase_budgets: Optional[PhaseBudgets] = None

    # ── transcript shaping (optional) ────────────────────────────────
    # Executor/critic prompt window caps. None reads the environment once;
    # tests inject a fixed TranscriptBudget.
    transcript_budget: Optional["TranscriptBudget"] = None

    # ── step observer port (optional) ────────────────────────────────
    # Default per-runtime observer for live step events; a per-run observer
    # can also be attached on AgentRunContext.on_step. Both are advisory.
    on_step: Optional[Callable[[Dict[str, Any]], None]] = None

    # ── agent profile (optional, v9.9.7) ─────────────────────────────
    # How hard the loop works to keep a weak model on contract. None selects
    # per-run from the executing model id (``profile_for_model``); tests
    # inject a fixed profile.
    agent_profile: Optional["AgentProfile"] = None

    # ── permission mode port (optional, v9.9.8) ──────────────────────
    # The autonomy dial. Either a static mode, or a resolver callable that
    # accepts ``user_email``/``workspace_id`` scope kwargs (preferred) or no
    # arguments at all — see ``call_mode_source``. ``None`` means strict,
    # which is exactly the pre-9.9.8 behaviour.
    permission_mode: Any = None

    # ── Self-Model port (optional, v11.2.0) ──────────────────────────
    # What the Brain has learned about its owner, injected into the executor
    # prompt. Either a fixed string or a resolver callable taking
    # ``user_email``/``workspace_id`` scope kwargs (or none at all). 11.1.0
    # built ``executor_prompt_for(self_model_summary=…)`` and then had nothing
    # to pass it, because the runtime held its executor prompt as a fixed
    # string; this is the port that was missing. ``None`` — and an empty
    # summary — produce exactly the prompt bytes the loop produced before it
    # existed.
    self_model_summary: Any = None
