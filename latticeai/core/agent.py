"""Single-agent runtime — the Discover→Plan→Implement→Verify state machine.

This module is the deep single-agent loop: a small interface (``AgentDeps`` ports +
``SingleAgentRuntime.run_to_completion``) over the whole role-phased state machine
(planner → executor → critic → rollback → memory). It carries no FastAPI,
no globals, and no I/O of its own — every collaborator is injected through
``AgentDeps``.

Two adapters justify the seam:

* production wires ``AgentDeps`` from ``latticeai.server_app``'s ``LLMRouter``, governance
  map, audit log, and prompts;
* tests pass fake ports (an LLM that returns canned JSON, a recording tool
  executor) and drive a full PLAN→EXECUTE→VERIFY→DONE cycle without a server.

HTTP concerns — request parsing, chat-history persistence, response shaping,
scheduling the background memory update — stay in the app layer. This module
only owns the state machine.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

from lattice_brain.runtime.hooks import dispatch_tool
from lattice_brain.runtime.contracts import runtime_boundary_contract, single_agent_contract
from latticeai.core.agent_trace import LoopTrace
from latticeai.core.file_generation import (
    infer_file_target,
    infer_project_manifest,
    sanitize_write_content,
)
from latticeai.core.tool_registry import SCOPED_KNOWLEDGE_TOOLS
from latticeai.tools import ToolError


class AgentState(str, Enum):
    IDLE             = "IDLE"
    PLANNING         = "PLANNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING        = "EXECUTING"
    VERIFYING        = "VERIFYING"
    FAILED           = "FAILED"
    ROLLBACK         = "ROLLBACK"
    # Terminal, non-success: the run ended but completion could not be
    # verified (critic unavailable/unparseable, or a PASS with no execution
    # evidence). Never presented as success — the user must check the result.
    NEEDS_REVIEW     = "NEEDS_REVIEW"
    DONE             = "DONE"


# Terminal states — the agent loop exits when reaching one of these
AGENT_TERMINAL_STATES: FrozenSet[AgentState] = frozenset(
    {AgentState.DONE, AgentState.FAILED, AgentState.NEEDS_REVIEW}
)


class AgentRunContext:
    """Mutable state carrier passed through all agent phases."""
    __slots__ = ("state", "plan", "transcript", "retry_count",
                 "state_history", "corrections", "final_message", "rollback_log",
                 "executing_model", "reviewing_model", "approved_by_human", "trace",
                 "on_step", "project_context")

    def __init__(self) -> None:
        self.state:           AgentState   = AgentState.IDLE
        self.trace:           LoopTrace    = LoopTrace()
        self.plan:            dict         = {}
        self.transcript:      list         = []
        self.retry_count:     int          = 0
        self.state_history:   list         = []
        self.corrections:     list         = []
        self.final_message:   str          = ""
        self.rollback_log:    list         = []
        self.executing_model: Optional[str] = None
        self.reviewing_model: Optional[str] = None
        self.approved_by_human: bool       = False
        # Per-run step observer (review Wave 1.1): the HTTP layer attaches a
        # callback here so live SSE clients see progress while EXECUTING.
        # Never serialized; a broken observer never breaks the loop.
        self.on_step: Optional[Callable[[Dict[str, Any]], None]] = None
        # Multi-turn project loop (v9.9.6): a prompt block describing where the
        # project stands — files already produced, open TODOs, the last honest
        # verification. Empty for a standalone run, which behaves exactly as
        # before. Set by the HTTP layer, read by plan/execute/verify.
        self.project_context: str = ""


_THINK_BLOCK_RE = re.compile(
    r"<(think|thinking|reasoning)>.*?</\1>", flags=re.DOTALL | re.IGNORECASE
)


def extract_action_details(raw: str) -> Tuple[Dict, List[str]]:
    """Parse one JSON action object out of an LLM response (tolerant of fences/prose).

    Returns ``(action, repairs)`` where ``repairs`` names every tolerance that
    was needed — the loop trace and the weak-model robustness harness consume
    it to measure how much help a given model needs.
    """
    repairs: List[str] = []
    # Small local models often prepend <think>...</think> reasoning that can
    # itself contain braces — drop it before locating the action object.
    text = _THINK_BLOCK_RE.sub("", raw).strip()
    if text != str(raw).strip():
        repairs.append("think_strip")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
        repairs.append("fence")
    elif not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
            repairs.append("slice")

    action: Any = None
    try:
        action = json.loads(text)
    except json.JSONDecodeError:
        # Second chance for the most common small-model JSON slips: trailing
        # commas before a closing brace/bracket.
        repaired = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            action = json.loads(repaired)
            repairs.append("trailing_comma")
        except json.JSONDecodeError as exc:
            # Last chance: weak models sometimes emit a Python dict literal
            # (single quotes, True/False/None). ast.literal_eval parses that
            # deterministically without evaluating code.
            try:
                literal = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                raise ValueError(f"Agent did not return valid JSON: {exc}") from exc
            if not isinstance(literal, dict):
                raise ValueError(f"Agent did not return valid JSON: {exc}") from exc
            action = literal
            repairs.append("python_literal")

    if not isinstance(action, dict) or "action" not in action:
        raise ValueError("Agent JSON must include an action field.")
    return action, repairs


def extract_action(raw: str) -> Dict:
    """Back-compat wrapper over :func:`extract_action_details`."""
    action, _ = extract_action_details(raw)
    return action


_FILE_CREATE_PLAN_ACTIONS = frozenset({"write_file", "generate_file"})


def _plan_misses_manifest(steps: List[Dict[str, Any]], manifest: Dict[str, Any]) -> bool:
    """True when a pure file-writing plan fails to cover the manifest's file types.

    Only pure file-creation plans are candidates for rewriting — a plan with
    read/search steps reflects real planner intent and stays untouched.
    """
    if any(s.get("action") not in _FILE_CREATE_PLAN_ACTIONS for s in steps):
        return False

    def _ext(path: Any) -> str:
        text = str(path or "")
        dot = text.rfind(".")
        return text[dot:].lower() if dot >= 0 else ""

    planned_exts = {
        _ext((s.get("args") or {}).get("path")) for s in steps
    }
    manifest_exts = {_ext(spec.get("path")) for spec in manifest.get("files", [])}
    return not manifest_exts.issubset(planned_exts)


def normalize_plan(plan: Any, user_message: str) -> Tuple[Dict[str, Any], List[str]]:
    """Enforce the minimal plan schema so execution never starts adrift.

    A weak planner that returns junk steps / a missing goal previously flowed
    straight into the executor, which then had to reconstruct intent from the
    raw request. Normalization keeps the loop honest: ``goal`` is always a
    non-empty string, ``steps`` only contains dicts with an ``action``, and an
    empty plan for an obvious file-creation request gets a deterministic
    single ``write_file`` step instead of leaving the executor to improvise.

    Returns ``(plan, fixes)`` where ``fixes`` names every applied repair —
    the loop trace records them so plan quality is observable per model.
    """
    fixes: List[str] = []
    if not isinstance(plan, dict):
        plan = {}
        fixes.append("plan_not_object")
    plan = dict(plan)

    goal = str(plan.get("goal") or "").strip()
    if not goal:
        plan["goal"] = user_message
        fixes.append("goal_defaulted")

    raw_steps = plan.get("steps")
    steps = [
        s for s in (raw_steps if isinstance(raw_steps, list) else [])
        if isinstance(s, dict) and s.get("action")
    ]
    if raw_steps and steps != raw_steps:
        fixes.append("steps_filtered")

    # Manifest-aware planning (review Wave 0.4): when the request is a
    # recognized multi-file project, the deterministic manifest — not the
    # planner's improvisation — decides the file set, exactly like the direct
    # chat path. Rewrites apply only when the plan is empty or is a pure
    # file-writing plan that misses part of the manifest, so a planner that
    # already covered every requested file type is left untouched.
    manifest = infer_project_manifest(user_message)
    if manifest:
        manifest_steps = [{
            "action": "write_file",
            "args": {"path": spec["path"]},
            "description": spec["brief"],
        } for spec in manifest["files"]]
        if not steps:
            steps = manifest_steps
            fixes.append("manifest_steps")
        elif _plan_misses_manifest(steps, manifest):
            steps = manifest_steps
            fixes.append("manifest_rewrite")

    if not steps:
        inferred = infer_file_target(user_message)
        if inferred:
            steps = [{
                "action": "write_file",
                "args": {"path": inferred},
                "description": f"Create {inferred} for: {user_message[:120]}",
            }]
            fixes.append("heuristic_file_step")
    plan["steps"] = steps

    try:
        estimated = int(plan.get("estimated_steps") or 0)
    except (TypeError, ValueError):
        estimated = 0
        fixes.append("estimated_steps_invalid")
    plan["estimated_steps"] = max(1, estimated, len(steps))
    plan["requires_approval"] = bool(plan.get("requires_approval", False))
    if not isinstance(plan.get("rollback_strategy"), str):
        plan["rollback_strategy"] = "none"
    return plan, fixes


_TRIVIAL_LEARNING_RE = re.compile(
    r"^(파일(을|이)?\s*(만들|생성|작성|저장)|작업(을|이)?\s*(완료|성공)|성공적으로"
    r"|task\s+(was\s+)?complet|file\s+(was\s+)?(creat|written|saved)"
    r"|(successfully\s+)?(created|completed|finished|done)\b)",
    re.IGNORECASE,
)


def filter_learnings(learnings: List[Any]) -> List[str]:
    """Drop trivial/duplicate learnings before they enter the brain.

    "파일을 만들었다"-class statements restate what the transcript already
    records and pollute recall. A learning survives when it is long enough to
    carry information and is not a bare completion announcement.
    """
    kept: List[str] = []
    seen: set = set()
    for raw in learnings or []:
        text = str(raw or "").strip()
        if len(text) < 12:
            continue
        if _TRIVIAL_LEARNING_RE.match(text) and len(text) < 48:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(text)
    return kept


def _truncate_strings(value: Any, limit: int) -> Any:
    """Deep-copy ``value`` with every string capped at ``limit`` chars.

    Long tool outputs (file bodies, command output) dominate executor prompt
    size without adding decision-relevant signal. The cap keeps the head of
    each string and names how much was dropped, so the model still sees what
    the value was — never a silent hole.
    """
    if isinstance(value, str):
        if len(value) <= limit:
            return value
        return value[:limit] + f"…[+{len(value) - limit} chars]"
    if isinstance(value, dict):
        return {k: _truncate_strings(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate_strings(v, limit) for v in value]
    return value


def compact_transcript(
    transcript: List[Dict[str, Any]],
    *,
    window: int = 8,
    result_chars: int = 700,
) -> List[Dict[str, Any]]:
    """Bounded executor view of a transcript (review Wave 0.3).

    The executor prompt previously embedded the *entire* transcript JSON every
    step — O(steps²) token growth that starved :class:`PhaseBudgets` on long
    runs and buried weak models in stale detail. This view keeps the most
    recent ``window`` steps in full (with string values capped at
    ``result_chars``) and reduces every older step to a one-line summary, so
    the prompt stays bounded while no step disappears entirely.
    """
    steps = list(transcript or [])
    if len(steps) <= window:
        return [_truncate_strings(step, result_chars) for step in steps]
    older, recent = steps[:-window], steps[-window:]
    summarized: List[Dict[str, Any]] = [{
        "summarized_older_steps": len(older),
        "note": "older steps compacted — full detail retained in the run record",
    }]
    for step in older:
        entry: Dict[str, Any] = {"state": step.get("state")}
        for key in ("action", "verdict", "retry_attempt"):
            if step.get(key) is not None:
                entry[key] = step.get(key)
        if step.get("error"):
            entry["error"] = str(step["error"])[:160]
        elif isinstance(step.get("result"), dict):
            entry["ok"] = True
            path = step["result"].get("path") or (step.get("args") or {}).get("path")
            if path:
                entry["path"] = str(path)
        summarized.append(entry)
    summarized.extend(_truncate_strings(step, result_chars) for step in recent)
    return summarized


def files_written(
    transcript: List[Dict[str, Any]],
    file_create_actions: FrozenSet[str],
) -> List[str]:
    """Ordered unique paths of files this run successfully wrote (review L5).

    Later executor steps get this as explicit context, so "만들고 이어서
    설명해" multi-step work sees its own output instead of a stale
    workspace picture.
    """
    seen: List[str] = []
    for step in transcript:
        if step.get("state") != AgentState.EXECUTING.value:
            continue
        if step.get("action") not in file_create_actions:
            continue
        if not isinstance(step.get("result"), dict):
            continue
        path = step["result"].get("path") or (step.get("args") or {}).get("path")
        if path and str(path) not in seen:
            seen.append(str(path))
    return seen


def artifact_checklist(
    transcript: List[Dict[str, Any]],
    file_create_actions: FrozenSet[str],
) -> List[Dict[str, Any]]:
    """Deterministic artifact facts for the critic (review L4).

    The critic previously judged file work from prose alone; this surfaces
    the sanitize/repair honesty flags per written file so a repaired
    placeholder can never pass as a fulfilled request unchecked.
    """
    checklist: List[Dict[str, Any]] = []
    for step in transcript:
        if step.get("state") != AgentState.EXECUTING.value:
            continue
        if step.get("action") not in file_create_actions:
            continue
        if not isinstance(step.get("result"), dict):
            continue
        path = step["result"].get("path") or (step.get("args") or {}).get("path")
        if not path:
            continue
        sanitize_meta = step.get("content_sanitize") or {}
        checklist.append({
            "path": str(path),
            "sanitized": bool(sanitize_meta.get("sanitized")),
            "repaired": bool(sanitize_meta.get("repaired")),
        })
    return checklist


# Explicit requirement lines a user writes out: "- 다크모드", "1. 검색 기능",
# "* dark mode". Free prose is deliberately NOT parsed — a wrong requirement
# is worse than no requirement.
_REQUIREMENT_LINE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.{3,120})$", re.MULTILINE)


def requirement_coverage(
    user_message: str,
    transcript: List[Dict[str, Any]],
    file_create_actions: FrozenSet[str],
) -> Dict[str, Any]:
    """Did the run produce what the request actually asked for? (review 루프 §2)

    The critic judged prose against prose, so "make an HTML+CSS+JS todo app"
    could pass with one file written. This is the deterministic half of the
    answer, built from two sources that are honest about their own limits:

    * **manifest files** — when the request maps to a known multi-file project
      (:func:`infer_project_manifest`), every declared path must have been
      written. A missing manifest file is a *hard* miss: it is not a matter of
      taste whether ``style.css`` exists.
    * **explicit requirement lines** — bullet/numbered lines the user wrote
      out. These are reported to the critic as a checklist but never block on
      their own: matching a feature to a transcript is a judgement call, and
      guessing it wrong would either fake completion or block real work.

    Returns ``{"files": {...}, "requirements": [...], "missing_files": [...],
    "complete": bool}`` where ``complete`` is false only when a declared
    manifest file is missing.
    """
    written = files_written(transcript, file_create_actions)
    written_names = {Path(path).name.lower() for path in written}
    manifest = infer_project_manifest(user_message) or {}
    declared = [str(spec.get("path") or "") for spec in manifest.get("files", [])]
    missing = [
        path for path in declared
        if path and Path(path).name.lower() not in written_names
    ]
    requirements = [
        line.strip()
        for line in _REQUIREMENT_LINE_RE.findall(str(user_message or ""))
    ][:10]
    return {
        "files": {"declared": declared, "written": written},
        "missing_files": missing,
        "requirements": requirements,
        "complete": not missing,
    }


def _format_requirement_coverage(coverage: Dict[str, Any]) -> str:
    """Requirement facts for the critic prompt, or "" when there is nothing."""
    lines: List[str] = []
    declared = coverage["files"]["declared"]
    if declared:
        written = set(coverage["files"]["written"])
        lines.append("Requested files (deterministic, from the request):")
        for path in declared:
            got = any(Path(item).name.lower() == Path(path).name.lower() for item in written)
            lines.append(f"- {path}: {'written' if got else 'MISSING'}")
    if coverage["requirements"]:
        lines.append(
            "Requirements the user listed explicitly — check each one against "
            "the artifacts, not against the plan:"
        )
        lines.extend(f"- {item}" for item in coverage["requirements"])
    return "\n\n" + "\n".join(lines) if lines else ""


def _format_artifact_checklist(checklist: List[Dict[str, Any]]) -> str:
    lines = []
    for item in checklist:
        state = (
            "auto-REPAIRED scaffold" if item["repaired"]
            else ("sanitized model output" if item["sanitized"] else "written as produced")
        )
        lines.append(f"- {item['path']}: {state}")
    return (
        "Artifact checklist (deterministic, from the transcript):\n"
        + "\n".join(lines)
        + "\nVerify each artifact actually fulfills the user's request. An "
        "auto-repaired scaffold is NOT completion unless its content "
        "satisfies what was asked."
    )


@dataclass(frozen=True)
class TranscriptBudget:
    """Executor/critic prompt shaping caps (review Wave 0.3).

    ``window`` full recent steps for the executor; per-string caps keep tool
    output bodies from dominating either prompt. Overridable through the same
    ``Config.from_env`` pattern as :class:`PhaseBudgets`.
    """

    window: int = 8
    result_chars: int = 700
    verify_chars: int = 1200

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "TranscriptBudget":
        from latticeai.core.config import _int

        if env is None:
            import os

            env = os.environ

        def cap(key: str, default: int, floor: int) -> int:
            return max(floor, _int(env, key, default))

        return cls(
            window=cap("LATTICEAI_AGENT_TRANSCRIPT_WINDOW", cls.window, 2),
            result_chars=cap("LATTICEAI_AGENT_TRANSCRIPT_CHARS", cls.result_chars, 120),
            verify_chars=cap("LATTICEAI_AGENT_VERIFY_CHARS", cls.verify_chars, 200),
        )


@dataclass(frozen=True)
class PhaseBudgets:
    """Per-phase token budgets for the agent loop.

    One shared budget let a weak model burn everything on planning prose and
    reach EXECUTE with nothing left. Each role phase now has its own cap, so
    a verbose planner can never starve execution or verification. Defaults
    match the historical hardcoded values; every cap is overridable through
    the ``Config.from_env`` environment pattern.
    """

    plan_tokens: int = 1024
    execute_tokens: int = 4096
    verify_tokens: int = 512
    memory_tokens: int = 256

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "PhaseBudgets":
        from latticeai.core.config import _int

        if env is None:
            import os

            env = os.environ

        def cap(key: str, default: int) -> int:
            # A misconfigured/absurd value must not brick the loop: floor at a
            # budget that still fits one JSON action object.
            return max(128, _int(env, key, default))

        return cls(
            plan_tokens=cap("LATTICEAI_AGENT_PLAN_TOKENS", cls.plan_tokens),
            execute_tokens=cap("LATTICEAI_AGENT_EXECUTE_TOKENS", cls.execute_tokens),
            verify_tokens=cap("LATTICEAI_AGENT_VERIFY_TOKENS", cls.verify_tokens),
            memory_tokens=cap("LATTICEAI_AGENT_MEMORY_TOKENS", cls.memory_tokens),
        )


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
    policy_for: Callable[[str, dict], dict]        # name, args -> governance policy
    risk_level: Callable[[dict], str]              # policy -> "low"|"medium"|"high"
    check_role: Callable[[str, str], None]         # tool_name, user -> raises if not allowed
    tool_governance: Dict[str, dict]               # name -> policy (for auto_approve set)
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


class SingleAgentRuntime:
    """Drives the agent state machine over injected :class:`AgentDeps`."""

    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps
        self._env_phase_budgets: Optional[PhaseBudgets] = None
        self._env_transcript_budget: Optional[TranscriptBudget] = None

    @property
    def phase_budgets(self) -> PhaseBudgets:
        # getattr twice: partially-constructed runtimes/deps (tests build them
        # via __new__ or minimal fakes) still get working default budgets.
        injected = getattr(self.deps, "phase_budgets", None)
        if injected is not None:
            return injected
        if getattr(self, "_env_phase_budgets", None) is None:
            self._env_phase_budgets = PhaseBudgets.from_env()
        return self._env_phase_budgets

    @property
    def transcript_budget(self) -> TranscriptBudget:
        injected = getattr(self.deps, "transcript_budget", None)
        if injected is not None:
            return injected
        if getattr(self, "_env_transcript_budget", None) is None:
            self._env_transcript_budget = TranscriptBudget.from_env()
        return self._env_transcript_budget

    def _emit_step(self, ctx: AgentRunContext, phase: str, event: str, **details: Any) -> None:
        """Fire the per-run / deps step observers (review Wave 1.1).

        Observers power the live step timeline in the UI. They are pure
        telemetry: any observer failure is logged and swallowed — the loop
        itself must never notice.
        """
        payload: Dict[str, Any] = {"phase": phase, "event": event}
        for key, value in details.items():
            if value is not None:
                payload[key] = value
        for observer in (getattr(ctx, "on_step", None), getattr(self.deps, "on_step", None)):
            if observer is None:
                continue
            try:
                observer(dict(payload))
            except Exception as exc:  # noqa: BLE001 — observers are advisory
                logging.warning("agent step observer failed: %s", exc)

    @staticmethod
    def _project_block(ctx: AgentRunContext) -> str:
        """Project-session context for prompts, or "" for a standalone run.

        Multi-turn project loop (v9.9.6): a later run must see the files the
        project already produced and what is still open, instead of planning
        from a blank workspace every time.
        """
        summary = str(getattr(ctx, "project_context", "") or "").strip()
        return f"\n\n[PROJECT SESSION]\n{summary}" if summary else ""

    def boundary(self) -> Dict[str, Any]:
        return runtime_boundary_contract(
            name="SingleAgentRuntime",
            runtime="single_agent",
            entrypoint="latticeai.core.agent.SingleAgentRuntime",
            surface="/agent",
            owns="single-agent PLAN / EXECUTE / VERIFY state machine over injected ports",
            compatibility_aliases=[],
        )

    def config(self) -> Dict[str, Any]:
        return {
            "boundary": self.boundary(),
            "states": [state.value for state in AgentState],
            "terminal_states": sorted(state.value for state in AGENT_TERMINAL_STATES),
            "execution_mode": "injected_ports",
        }

    def contract(self, ctx: AgentRunContext, req: Any, *, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Expose the shared agent-run contract for the single-agent loop."""
        return single_agent_contract(ctx=ctx, goal=getattr(req, "message", ""), run_id=run_id)

    # ── PLAN ─────────────────────────────────────────────────────────
    async def plan(
        self, ctx: AgentRunContext, req: Any, lang_hint: str, current_user: str,
        model_id: Optional[str] = None,
    ) -> None:
        """PLAN: Planner role produces a structured plan JSON."""
        d = self.deps
        project_block = self._project_block(ctx)
        context = (
            f"{d.planner_prompt}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n"
            f"Workspace root: {d.agent_root}{project_block}\n\n"
            f"User request: {req.message}"
        )
        raw = await d.generate_as(
            model_id,
            message="Produce a JSON execution plan for this request.",
            context=context, max_tokens=self.phase_budgets.plan_tokens, temperature=0.1,
        )
        ctx.trace.llm_call("plan", model=model_id)
        try:
            plan, plan_repairs = extract_action_details(str(raw))
            ctx.trace.repair("plan", repairs=plan_repairs)
        except ValueError as exc:
            ctx.trace.parse_error("plan", error=str(exc), recovered=True)
            plan = {
                "action": "plan", "state": "PLAN",
                "goal": req.message, "steps": [],
                "requires_approval": False, "rollback_strategy": "none", "estimated_steps": 1,
            }
        plan, plan_fixes = normalize_plan(plan, req.message)
        if plan_fixes:
            ctx.trace.repair("plan", repairs=plan_fixes)
        ctx.plan = plan
        ctx.transcript.append({
            "state": AgentState.PLANNING.value,
            "goal": plan.get("goal", req.message),
            "steps": plan.get("steps", []),
            "requires_approval": plan.get("requires_approval", False),
            "rollback_strategy": plan.get("rollback_strategy", "none"),
            "estimated_steps": plan.get("estimated_steps", 1),
            **({"plan_fixes": plan_fixes} if plan_fixes else {}),
        })
        self._emit_step(
            ctx, "plan", "planned",
            goal=str(plan.get("goal") or "")[:200],
            steps=len(plan.get("steps") or []),
            requires_approval=bool(plan.get("requires_approval", False)),
        )
        ctx.state = AgentState.WAITING_APPROVAL

    # ── APPROVAL ─────────────────────────────────────────────────────
    def approval_requirements(self, ctx: AgentRunContext) -> Dict[str, Any]:
        """Read-only preview of the approval gate for a planned run.

        Shares the exact predicate :meth:`approve` enforces, so the HTTP
        layer can pause a run as ``awaiting_approval`` (with a plan summary
        for the user) instead of letting it fail closed — without ever
        weakening the gate itself.
        """
        d = self.deps
        auto_approve_tools = {name for name, p in d.tool_governance.items() if p["auto_approve"]}
        # Governor-managed tools never hard-block the plan: each call is
        # classified at execution time — additive creates run, mutations and
        # deletions of existing content become review proposals.
        governed_tools = (
            frozenset(getattr(d.change_governor, "governed_tools", frozenset()))
            if d.change_governor is not None else frozenset()
        )
        steps = ctx.plan.get("steps", [])
        non_auto = [
            s.get("action") for s in steps
            if s.get("action") not in auto_approve_tools
            and s.get("action") not in governed_tools
        ]
        requires = bool(ctx.plan.get("requires_approval", False)) or bool(non_auto)
        lines = [
            f"{index}. {step.get('description') or step.get('action') or '?'}"
            for index, step in enumerate(steps, start=1)
        ]
        summary = str(ctx.plan.get("goal") or "").strip()
        if lines:
            summary = (summary + "\n" if summary else "") + "\n".join(lines)
        return {
            "requires_approval": requires,
            "non_auto_steps": non_auto,
            "plan_summary": summary,
        }

    def approve(self, ctx: AgentRunContext, current_user: str, *, approved_by_human: bool = False) -> None:
        """APPROVAL: Check governance, log decision, auto-approve (future: UI prompt)."""
        d = self.deps
        requirements = self.approval_requirements(ctx)
        non_auto = requirements["non_auto_steps"]
        requires = requirements["requires_approval"]

        ctx.transcript.append({
            "state": AgentState.WAITING_APPROVAL.value,
            "requires_approval": requires,
            "non_auto_approve_steps": non_auto,
            "decision": "human_approved" if requires and approved_by_human else ("blocked_pending_approval" if requires else "auto_approved"),
        })
        decision = "human_approved" if requires and approved_by_human else ("blocked_pending_approval" if requires else "auto_approved")
        ctx.trace.decision("approve", decision=decision, non_auto_steps=len(non_auto))
        self._emit_step(ctx, "approval", "decision", decision=decision)
        d.audit(
            "agent_approval", user_email=current_user,
            requires_approval=requires,
            non_auto_steps=non_auto,
            decision=decision,
        )
        if requires and not approved_by_human:
            ctx.final_message = (
                "이 작업에는 명시 승인이 필요한 도구가 포함되어 있어 자동 실행을 중단했습니다. "
                "human_in_loop 승인 흐름으로 다시 실행해 주세요."
            )
            ctx.state = AgentState.FAILED
            return
        ctx.approved_by_human = bool(approved_by_human)
        ctx.state = AgentState.EXECUTING

    # ── EXECUTE ──────────────────────────────────────────────────────
    async def execute(
        self, ctx: AgentRunContext, req: Any, lang_hint: str,
        current_user: str, max_steps: int, model_id: Optional[str] = None,
    ) -> None:
        """EXECUTE: Executor role calls tools one at a time until final or budget exhausted."""
        d = self.deps
        exec_count = sum(1 for s in ctx.transcript if s.get("state") == AgentState.EXECUTING.value)
        budget = max(1, max_steps - exec_count)
        parse_failures = 0

        for _ in range(budget):
            request_workspace = getattr(req, "workspace_id", None)
            context = self._executor_context(ctx, req, lang_hint, current_user, request_workspace)
            raw = await d.generate_as(
                model_id,
                message="Execute the next step.",
                context=context, max_tokens=self.phase_budgets.execute_tokens,
                temperature=req.temperature,
            )
            ctx.trace.llm_call("execute", model=model_id)
            try:
                action, exec_repairs = extract_action_details(str(raw))
                ctx.trace.repair("execute", repairs=exec_repairs)
            except ValueError as exc:
                parse_failures += 1
                if self._note_parse_failure(ctx, raw, exc, parse_failures):
                    break
                continue

            name     = action.get("action")
            thoughts = str(action.get("thoughts") or "")[:600]
            args     = action.get("args") or {}

            if name in SCOPED_KNOWLEDGE_TOOLS:
                # Scope is server-owned, never model-owned. Overwrite any
                # claimed values before policy evaluation, audit, and dispatch.
                args = dict(args)
                args["workspace_id"] = request_workspace or "personal"
                args["user_email"] = current_user or "local"

            if name == "final":
                ctx.final_message = action.get("message", "작업을 완료했습니다.")
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": "final", "thoughts": thoughts,
                })
                ctx.trace.decision("execute", decision="final")
                self._emit_step(ctx, "execute", "final")
                ctx.state = AgentState.VERIFYING
                return

            # Loop guard
            if self._is_repeated_create(ctx, name, args):
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "error": "LOOP_DETECTED: identical action+args repeated — halted.",
                })
                ctx.trace.decision("execute", decision="loop_detected", tool=name)
                self._emit_step(ctx, "execute", "blocked", action=name, reason="loop_detected")
                break

            if name == "clear_history":
                result = d.clear_history(args.get("keep_last", 0))
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value, "action": name,
                    "thoughts": thoughts, "args": args, "result": result,
                })
                self._emit_step(ctx, "execute", "tool", action=name, ok=True)
                continue

            policy = d.policy_for(name, args)
            risk   = d.risk_level(policy)

            proposed, governor_allows_additive = self._governor_review(
                ctx, name, thoughts, args, policy, risk, current_user, request_workspace,
                conversation_id=getattr(req, "conversation_id", None),
            )
            if proposed:
                continue

            if self._blocked_by_gates(
                ctx, req, name, thoughts, args, policy, risk,
                current_user, governor_allows_additive,
            ):
                continue

            self._dispatch_step(ctx, name, thoughts, args, policy, risk, current_user)

        ctx.state = AgentState.VERIFYING

    def _executor_context(
        self, ctx: AgentRunContext, req: Any, lang_hint: str,
        current_user: str, request_workspace: Optional[str],
    ) -> str:
        """Assemble one executor turn's prompt (plan, corrections, recent chat)."""
        d = self.deps
        # Only the latest corrections steer the next attempt — stale hints
        # from earlier retries dilute weak models (review Wave 0.3).
        active_corrections = ctx.corrections[-3:]
        corrections_hint = (
            "\n\nCritic corrections from previous attempt:\n"
            + "\n".join(f"- {c}" for c in active_corrections)
        ) if active_corrections else ""

        recent_kwargs = {
            "conversation_id": req.conversation_id,
            "user_email": current_user or None,
        }
        if request_workspace is not None:
            recent_kwargs["workspace_id"] = request_workspace
        recent_conversation = d.recent_chat_context(**recent_kwargs) or "(none)"
        budget = self.transcript_budget
        bounded_transcript = compact_transcript(
            ctx.transcript,
            window=budget.window,
            result_chars=budget.result_chars,
        )
        # Mid-run workspace awareness (review L5): later steps must see what
        # this run already produced instead of a stale workspace picture.
        written = files_written(ctx.transcript, d.file_create_actions)
        written_hint = (
            "\n\nFiles written by this run so far (they exist in the workspace now):\n"
            + "\n".join(f"- {path}" for path in written)
        ) if written else ""
        return (
            f"{d.executor_prompt}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n"
            f"Workspace root: {d.agent_root}{self._project_block(ctx)}\n\n"
            f"PLAN:\n{json.dumps(ctx.plan, ensure_ascii=False)}{written_hint}\n\n"
            f"Recent conversation:\n{recent_conversation}\n\n"
            f"User request: {req.message}{corrections_hint}\n\n"
            f"Execution transcript:\n{json.dumps(bounded_transcript, ensure_ascii=False, indent=2)}"
        )

    def _note_parse_failure(
        self, ctx: AgentRunContext, raw: Any, exc: ValueError, parse_failures: int,
    ) -> bool:
        """Record one executor parse slip; True when the run should stop retrying."""
        ctx.transcript.append({
            "state": AgentState.EXECUTING.value, "action": "parse_error",
            "raw": str(raw)[:400], "error": str(exc),
        })
        if parse_failures >= 3:
            ctx.trace.parse_error("execute", error=str(exc), recovered=False)
            self._emit_step(ctx, "execute", "parse_error", recovered=False)
            return True
        ctx.trace.parse_error("execute", error=str(exc), recovered=True)
        self._emit_step(ctx, "execute", "parse_error", recovered=True)
        # Weak models often need one concrete reminder of the wire
        # format; feed it through the corrections channel and retry
        # instead of aborting the whole run on the first slip.
        hint = (
            'Your last reply was not a single JSON action object. Reply with '
            'EXACTLY one JSON object like {"thoughts": "...", "action": '
            '"tool_name", "args": {...}} and nothing else.'
        )
        if parse_failures >= 2:
            # Escalate: name the valid tools so the model stops
            # inventing action names or prose.
            valid = ", ".join(sorted(self.deps.tool_governance.keys()))
            hint = (
                f"{hint} Valid action values are: {valid}, final. "
                'Use {"action": "final", "message": "..."} to finish.'
            )
        if hint not in ctx.corrections:
            ctx.corrections.append(hint)
            ctx.trace.correction("execute", hint=hint)
        return False

    def _is_repeated_create(self, ctx: AgentRunContext, name: Any, args: dict) -> bool:
        """Loop guard: the same file-create action+args re-issued right after a result."""
        exec_steps = [s for s in ctx.transcript if s.get("state") == AgentState.EXECUTING.value]
        last = exec_steps[-1] if exec_steps else None
        return bool(
            name in self.deps.file_create_actions and last
            and last.get("action") == name
            and (last.get("args") or {}) == args
            and "result" in last
        )

    def _governor_review(
        self, ctx: AgentRunContext, name: str, thoughts: str, args: dict,
        policy: dict, risk: str, current_user: str, request_workspace: Optional[str],
        conversation_id: Optional[str] = None,
    ) -> Tuple[bool, bool]:
        """Central change-class governance: create-new runs with minimal
        friction, change/delete-existing becomes a review proposal.

        Returns ``(proposed, governor_allows_additive)``: ``proposed`` means the
        step was staged as a proposal (skip execution); ``allows_additive`` lets
        an additive create pass the classic approval gate.
        """
        d = self.deps
        if d.change_governor is None:
            return False, False
        verdict = d.change_governor.review(
            name, args, policy=dict(policy),
            user_email=current_user, workspace_id=request_workspace,
            conversation_id=conversation_id,
        )
        if verdict is not None and verdict.get("decision") == "proposed":
            proposal = verdict.get("proposal") or {}
            ctx.trace.tool("execute", name=name, outcome="proposed", risk=risk)
            self._emit_step(ctx, "execute", "proposed", action=name)
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": {k: v for k, v in args.items() if k != "content"},
                "risk": risk, "governance": dict(policy),
                "result": {
                    "proposed": True,
                    "proposal_id": proposal.get("id"),
                    "note": "기존 내용을 바꾸는 작업이라 변경 제안으로 저장했습니다. 검토함에서 승인하면 적용됩니다.",
                },
            })
            d.audit(
                "agent_change_proposed", user_email=current_user,
                action=name, proposal_id=proposal.get("id"),
                change_class=(verdict.get("classification") or {}).get("change_class"),
            )
            return True, False
        return False, (verdict is not None and verdict.get("decision") == "allow_additive")

    def _blocked_by_gates(
        self, ctx: AgentRunContext, req: Any, name: str, thoughts: str, args: dict,
        policy: dict, risk: str, current_user: str, governor_allows_additive: bool,
    ) -> bool:
        """Classic destructive / explicit-approval gates; True when the step was blocked."""
        d = self.deps
        if policy["risk"] == "destructive":
            ctx.trace.tool("execute", name=name, outcome="blocked_destructive", risk=risk)
            self._emit_step(ctx, "execute", "blocked", action=name, reason="destructive")
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args, "risk": risk,
                "governance": dict(policy),
                "error": f"BLOCKED: destructive action '{name}' not permitted in agent mode.",
            })
            d.audit(
                "agent_blocked", user_email=current_user, source=getattr(req, "source", None) or "agent",
                action=name, reason="destructive", governance=dict(policy),
            )
            return True

        if not policy["auto_approve"] and not ctx.approved_by_human and not governor_allows_additive:
            d.audit(
                "agent_exec", user_email=current_user, source=getattr(req, "source", None) or "agent",
                state=AgentState.EXECUTING.value, action=name, risk=risk,
                shell=policy["shell"], network=policy["network"],
                destructive=policy["destructive"], sandbox=policy["sandbox"],
                rollback=policy["rollback"],
                args={k: v for k, v in args.items() if k != "content"},
            )
            ctx.trace.tool("execute", name=name, outcome="blocked_approval", risk=risk)
            self._emit_step(ctx, "execute", "blocked", action=name, reason="approval")
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args, "risk": risk,
                "governance": dict(policy),
                "error": f"BLOCKED: action '{name}' requires explicit approval.",
            })
            return True
        return False

    def _dispatch_step(
        self, ctx: AgentRunContext, name: str, thoughts: str, args: dict,
        policy: dict, risk: str, current_user: str,
    ) -> None:
        """Role check + shared tool lifecycle, recorded on the transcript either way."""
        d = self.deps
        sanitize_meta: Optional[Dict[str, Any]] = None
        if name == "write_file" and isinstance(args.get("content"), str):
            # ArtifactWritePipeline: the executor's args.content is untrusted
            # model output. The same extract→validate→repair guarantee as the
            # direct chat path applies here, so a weak model driving the JSON
            # loop can never persist fenced/chatty/truncated payloads.
            cleaned, meta = sanitize_write_content(
                str(args.get("path") or ""), args["content"],
                user_request=str(ctx.plan.get("goal") or thoughts or name),
            )
            if meta.get("sanitized"):
                args = dict(args)
                args["content"] = cleaned
                sanitize_meta = meta
                ctx.trace.repair(
                    "execute",
                    repairs=[
                        "artifact_repair" if meta.get("repaired") else "artifact_sanitize"
                    ],
                )
        step_index = 1 + sum(
            1 for s in ctx.transcript
            if s.get("state") == AgentState.EXECUTING.value
            and s.get("action") not in (None, "final", "parse_error")
        )
        if (
            name in d.file_create_actions
            and d.snapshot_file is not None
            and args.get("path")
        ):
            # Pre-write snapshot (review L7): the first capture per path is
            # the true pre-run state — later writes to the same path must
            # not overwrite it. Best-effort: a snapshot failure never
            # blocks the write, it only narrows rollback options.
            path_str = str(args["path"])
            if not any(entry.get("path") == path_str for entry in ctx.rollback_log):
                try:
                    pre = d.snapshot_file(path_str)
                    ctx.rollback_log.append({"path": path_str, **(pre or {})})
                except Exception as exc:  # noqa: BLE001
                    logging.warning("pre-write snapshot failed for %s: %s", path_str, exc)
        try:
            d.check_role(name, current_user)
            # Shared tool lifecycle: pre_tool (may block) → execute → post_tool.
            result = dispatch_tool(
                d.hooks, name, args,
                lambda: d.execute_tool(name, args),
                user_email=current_user, source="agent",
            )
            ctx.trace.tool("execute", name=name, outcome="ok", risk=risk)
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args,
                "risk": risk, "governance": dict(policy), "result": result,
                **({"content_sanitize": sanitize_meta} if sanitize_meta else {}),
            })
            self._emit_step(
                ctx, "execute", "tool", action=name, ok=True, step=step_index,
                path=str(args.get("path")) if args.get("path") else None,
            )
        except (ToolError, KeyError, TypeError, PermissionError) as exc:
            ctx.trace.tool("execute", name=name, outcome="error", risk=risk)
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args,
                "risk": risk, "governance": dict(policy), "error": str(exc),
            })
            self._emit_step(
                ctx, "execute", "tool", action=name, ok=False, step=step_index,
                path=str(args.get("path")) if args.get("path") else None,
            )

    # ── VERIFY ───────────────────────────────────────────────────────
    def _has_execution_evidence(self, ctx: AgentRunContext) -> bool:
        """Deterministic evidence check: at least one executing step actually
        produced a result (tool ran, or a governed change was staged as a
        proposal). ``final``/parse-error/blocked steps carry no result and do
        not count — a critic PASS over an evidence-free transcript must not
        become DONE."""
        for step in ctx.transcript:
            if step.get("state") != AgentState.EXECUTING.value:
                continue
            if step.get("action") in (None, "final", "parse_error"):
                continue
            if isinstance(step.get("result"), dict):
                return True
        return False

    async def verify(
        self, ctx: AgentRunContext, req: Any, lang_hint: str, current_user: str,
        max_retry: int = 3, model_id: Optional[str] = None,
    ) -> None:
        """VERIFYING: Critic role evaluates transcript → DONE / EXECUTING (retry) / ROLLBACK / NEEDS_REVIEW / FAILED.

        Fail-closed: a critic whose output cannot be parsed (after one strict
        repair retry) never fabricates a PASS — the run terminates as
        NEEDS_REVIEW so the user is told to check the result themselves.
        """
        d = self.deps
        # The critic must see every step (evidence completeness), but not
        # every byte of tool output — long bodies are capped per string so
        # verification stays affordable on long runs (review Wave 0.3).
        verify_transcript = _truncate_strings(
            ctx.transcript, self.transcript_budget.verify_chars
        )
        # Deterministic artifact facts (review L4): the critic sees the
        # sanitize/repair honesty flags per written file, not just prose.
        checklist = artifact_checklist(ctx.transcript, d.file_create_actions)
        checklist_hint = (
            f"\n\n{_format_artifact_checklist(checklist)}" if checklist else ""
        )
        # Requirement coverage (review 루프 §2): the critic previously judged
        # "did this fulfill the request?" from prose alone. It now also sees
        # which requested files actually exist and which requirements the user
        # spelled out.
        coverage = requirement_coverage(
            req.message, ctx.transcript, d.file_create_actions
        )
        context = (
            f"{d.critic_prompt}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n\n"
            f"Original request: {req.message}\n"
            f"Plan goal: {ctx.plan.get('goal', req.message)}{checklist_hint}"
            f"{_format_requirement_coverage(coverage)}\n\n"
            f"Full transcript:\n{json.dumps(verify_transcript, ensure_ascii=False, indent=2)}"
        )
        raw = await d.generate_as(
            model_id,
            message="Review the execution transcript and return your verdict JSON.",
            context=context, max_tokens=self.phase_budgets.verify_tokens, temperature=0.1,
        )
        ctx.trace.llm_call("verify", model=model_id)
        verdict: Optional[Dict[str, Any]] = None
        try:
            verdict, verdict_repairs = extract_action_details(str(raw))
            ctx.trace.repair("verify", repairs=verdict_repairs)
        except ValueError as exc:
            # One strict repair retry — re-ask the critic for the exact wire
            # format instead of fabricating a verdict.
            ctx.trace.parse_error("verify", error=str(exc), recovered=True)
            strict_context = (
                f"{context}\n\n"
                "Your previous verdict was not parseable JSON. Reply with EXACTLY one "
                'JSON object like {"action": "verdict", "verdict": "PASS", '
                '"next_state": "DONE", "reason": "...", "corrections": []} '
                "and nothing else. verdict must be PASS or FAIL; next_state must be "
                "one of DONE, EXECUTING, ROLLBACK, FAILED."
            )
            raw = await d.generate_as(
                model_id,
                message="Return your verdict as one strict JSON object.",
                context=strict_context, max_tokens=self.phase_budgets.verify_tokens,
                temperature=0.0,
            )
            ctx.trace.llm_call("verify", model=model_id)
            try:
                verdict, verdict_repairs = extract_action_details(str(raw))
                ctx.trace.repair("verify", repairs=verdict_repairs)
            except ValueError as retry_exc:
                ctx.trace.parse_error("verify", error=str(retry_exc), recovered=False)
                verdict = None

        has_evidence = self._has_execution_evidence(ctx)

        if verdict is None:
            # Verifier unavailable — fail closed, never DONE.
            ctx.transcript.append({
                "state": AgentState.VERIFYING.value,
                "verdict": "UNAVAILABLE",
                "reason": "critic output unparseable after strict retry",
                "verifier_available": False,
                "verdict_valid": False,
                "evidence": has_evidence,
            })
            ctx.trace.decision(
                "verify", decision="verification_unavailable",
                verifier_available=False, verdict_valid=False, evidence=has_evidence,
            )
            self._emit_step(ctx, "verify", "verdict", verdict="UNAVAILABLE")
            ctx.final_message = (
                "검증을 완료하지 못했습니다 — 검증 모델의 응답을 해석할 수 없었습니다. "
                "실행 결과를 직접 확인해 주시고, 필요하면 다시 시도해 주세요."
            )
            ctx.state = AgentState.NEEDS_REVIEW
            return

        ctx.corrections = verdict.get("corrections", [])
        # Normalize legacy verdict next_state strings to current AgentState names
        raw_next = verdict.get("next_state", "")
        next_s = {"COMPLETE": "DONE", "RETRY": "EXECUTING"}.get(raw_next, raw_next)

        ctx.transcript.append({
            "state": AgentState.VERIFYING.value,
            "verdict":     verdict.get("verdict", ""),
            "reason":      verdict.get("reason", ""),
            "corrections": ctx.corrections,
            "confidence":  verdict.get("confidence", 0.9),
            "next_state":  next_s,
            "verifier_available": True,
            "verdict_valid": True,
            "evidence": has_evidence,
        })

        ctx.trace.decision(
            "verify", decision=str(verdict.get("verdict", "")), next_state=next_s,
            verifier_available=True, verdict_valid=True, evidence=has_evidence,
        )
        self._emit_step(
            ctx, "verify", "verdict",
            verdict=str(verdict.get("verdict", "")), next_state=next_s,
        )
        if verdict.get("verdict") == "PASS":
            # DONE requires both: a validly parsed PASS verdict AND
            # deterministic execution evidence in the transcript. A PASS over
            # an evidence-free run is not a completion.
            if not has_evidence:
                ctx.trace.decision("verify", decision="needs_review_no_evidence")
                ctx.final_message = (
                    "검증자는 통과를 보고했지만 실제 실행 근거(도구 실행 기록)가 없어 "
                    "완료로 처리하지 않았습니다. 결과를 직접 확인해 주세요."
                )
                ctx.state = AgentState.NEEDS_REVIEW
                return
            if not coverage["complete"]:
                # A PASS that leaves a *requested file* unwritten is not a
                # completion — this is a fact, not a judgement, so it is
                # enforced rather than merely reported to the critic.
                missing = ", ".join(coverage["missing_files"])
                ctx.trace.decision(
                    "verify", decision="needs_review_missing_files",
                    missing=len(coverage["missing_files"]),
                )
                ctx.transcript.append({
                    "state": AgentState.VERIFYING.value,
                    "requirement_coverage": coverage,
                })
                ctx.final_message = (
                    f"요청한 파일 중 일부가 만들어지지 않아 완료로 처리하지 않았습니다: {missing}"
                )
                ctx.state = AgentState.NEEDS_REVIEW
                return
            if not ctx.final_message:
                ctx.final_message = verdict.get("reason", "작업이 완료되었습니다.")
            ctx.state = AgentState.DONE
        elif next_s == "ROLLBACK":
            ctx.state = AgentState.ROLLBACK
        elif next_s == "EXECUTING":
            if ctx.retry_count >= max_retry:
                ctx.final_message = "처리 중 문제가 발생했습니다. 다시 시도해 주세요."
                ctx.state = AgentState.FAILED
            else:
                ctx.retry_count += 1
                ctx.trace.retry("verify", attempt=ctx.retry_count)
                ctx.transcript.append({
                    "state": AgentState.EXECUTING.value,
                    "retry_attempt": ctx.retry_count,
                    "corrections": ctx.corrections,
                })
                ctx.state = AgentState.EXECUTING
        elif next_s == "DONE":
            # Contradictory verdict: the critic asked for DONE without a PASS.
            # The loose "or next_state == DONE" success path is gone — this is
            # a non-success that the user must review.
            ctx.trace.decision("verify", decision="needs_review_inconsistent_verdict")
            ctx.final_message = (
                "검증 결과가 일관되지 않아 완료로 처리하지 않았습니다. "
                "실행 결과를 직접 확인해 주세요."
            )
            ctx.state = AgentState.NEEDS_REVIEW
        else:
            ctx.final_message = verdict.get("reason", "검증자가 인식되지 않은 다음 상태를 반환했습니다.")
            ctx.state = AgentState.FAILED

    # ── ROLLBACK ─────────────────────────────────────────────────────
    def _snapshot_for(self, ctx: AgentRunContext, path: str) -> Optional[Dict[str, Any]]:
        for entry in ctx.rollback_log:
            if entry.get("path") == path:
                return entry
        return None

    def _rollback_one(self, ctx: AgentRunContext, path: str, gov: Dict[str, Any]) -> Dict[str, Any]:
        """Recover one path: git when governed and available, else the
        pre-write snapshot, else an honest ``mode="none"`` (review L7)."""
        d = self.deps
        if gov.get("rollback") == "git" and d.rollback_file is not None:
            try:
                result = dict(d.rollback_file(str(path)))
            except Exception as exc:  # noqa: BLE001
                result = {"path": path, "ok": False, "error": str(exc)}
            if result.get("ok"):
                result["mode"] = "git"
                return result
        snapshot = self._snapshot_for(ctx, str(path))
        if snapshot is not None and d.restore_snapshot is not None and not snapshot.get("too_large"):
            content = snapshot.get("content") if snapshot.get("existed") else None
            try:
                restored = dict(d.restore_snapshot(str(path), content))
            except Exception as exc:  # noqa: BLE001
                restored = {"path": path, "ok": False, "error": str(exc)}
            restored.setdefault("path", path)
            restored["mode"] = "snapshot"
            return restored
        return {
            "path": path, "ok": False, "mode": "none",
            "error": "no rollback available (git not applicable, no usable snapshot)",
        }

    def rollback(self, ctx: AgentRunContext, current_user: str) -> None:
        """ROLLBACK: recover written files (git → snapshot → none), then FAILED."""
        d = self.deps
        rolled: List[dict] = []
        seen_paths: set = set()
        for step in ctx.transcript:
            if step.get("state") != AgentState.EXECUTING.value:
                continue
            if not isinstance(step.get("result"), dict):
                continue
            gov = step.get("governance", {}) or {}
            path = step["result"].get("path") or (step.get("args") or {}).get("path", "")
            if not path or str(path) in seen_paths:
                continue
            if gov.get("rollback") != "git" and step.get("action") not in d.file_create_actions:
                continue
            seen_paths.add(str(path))
            rolled.append(self._rollback_one(ctx, str(path), gov))

        ctx.transcript.append({"state": AgentState.ROLLBACK.value, "rolled_back": rolled})
        ctx.trace.decision(
            "rollback", decision="rolled_back",
            attempted=len(rolled), recovered=sum(1 for r in rolled if r.get("ok")),
        )
        recovered = [f"{r['path']} ({r.get('mode')})" for r in rolled if r.get("ok")]
        ctx.final_message = (
            f"실행 실패로 롤백했습니다. 복구 파일: {recovered}"
            if recovered
            else "롤백을 시도했으나 복구할 파일이 없거나 git/스냅샷 복구 수단이 없습니다."
        )
        d.audit("agent_rollback", user_email=current_user, rolled_back=rolled)
        self._emit_step(ctx, "rollback", "rolled_back", recovered=len(recovered))
        # Rollback is a recovery from a failed verification — terminal state is FAILED
        ctx.state = AgentState.FAILED

    # ── MEMORY ───────────────────────────────────────────────────────
    async def memory_update(self, ctx: AgentRunContext, req: Any, current_user: str) -> None:
        """Background: Memory Updater role extracts learnings from a terminal run.

        Terminal-state learning policy (review §4.2 L6): DONE runs record what
        worked; FAILED / NEEDS_REVIEW runs record what went wrong — failure is
        exactly the experience worth remembering. The run status stored with
        the experience is the *actual* terminal state, never a blanket "ok".
        """
        d = self.deps
        terminal = ctx.state.value if ctx.state in AGENT_TERMINAL_STATES else "UNKNOWN"
        outcome_hint = (
            "The task completed successfully."
            if ctx.state == AgentState.DONE
            else (
                f"The task ended as {terminal} — extract what went wrong and "
                "what to do differently next time, not a success story."
            )
        )
        context = (
            f"{d.memory_updater_prompt}\n\n"
            f"Task: {req.message}\n"
            f"Terminal status: {terminal}. {outcome_hint}\n\n"
            f"Last 5 transcript steps:\n{json.dumps(ctx.transcript[-5:], ensure_ascii=False)}"
        )
        try:
            raw = await d.generate(
                message="Extract learnings from this completed task.",
                context=context, max_tokens=self.phase_budgets.memory_tokens, temperature=0.1,
            )
            mem = extract_action(str(raw))
            kept_learnings = filter_learnings(mem.get("learnings") or [])
            if mem.get("save_to_knowledge") and kept_learnings:
                learnings = "\n".join(kept_learnings)
                status_label = {
                    AgentState.DONE: "ok",
                    AgentState.NEEDS_REVIEW: "needs_review",
                    AgentState.FAILED: "failed",
                }.get(ctx.state, "unknown")
                if d.brain_memory is not None:
                    # This runtime is LLM-driven — its learnings are real
                    # experiences and enter the brain with provenance.
                    d.brain_memory.record_experience(
                        f"Agent: {req.message[:60]}",
                        learnings,
                        run={
                            "mode": "llm",
                            "status": status_label,
                            "agent_id": "agent:executor",
                            "steps": len(ctx.transcript),
                        },
                        user_email=current_user or None,
                    )
                else:
                    d.knowledge_save(
                        learnings,
                        folder="30_Projects",
                        title=f"Agent: {req.message[:60]}",
                    )
        except Exception as exc:
            # Never crash a completed run, but never swallow silently either.
            logging.warning("agent memory update failed: %s", exc)

    # ── DRIVE LOOP ───────────────────────────────────────────────────
    async def run_to_completion(
        self, ctx: AgentRunContext, req: Any, lang_hint: str,
        current_user: str, max_steps: int, max_retry: int,
    ) -> None:
        """Run EXECUTING → VERIFYING → ROLLBACK loop until a terminal state."""
        while ctx.state not in AGENT_TERMINAL_STATES:
            ctx.state_history.append(ctx.state.value)
            if len(ctx.state_history) > 200:
                ctx.final_message = "에이전트 상태 머신이 최대 반복(200)에 도달해 중단했습니다."
                ctx.state = AgentState.FAILED
                break

            if ctx.state == AgentState.EXECUTING:
                await self.execute(ctx, req, lang_hint, current_user, max_steps,
                                   model_id=ctx.executing_model)
            elif ctx.state == AgentState.VERIFYING:
                await self.verify(ctx, req, lang_hint, current_user, max_retry,
                                  model_id=ctx.reviewing_model)
            elif ctx.state == AgentState.ROLLBACK:
                self.rollback(ctx, current_user)
            else:
                ctx.state = AgentState.FAILED

        ctx.state_history.append(ctx.state.value)
        self._emit_step(ctx, "terminal", "state", state=ctx.state.value)
