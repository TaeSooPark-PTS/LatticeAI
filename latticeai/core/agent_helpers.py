"""Pure helpers for the single-agent runtime.

Extracted from :mod:`latticeai.core.agent` so the state machine module
owns only the loop. Every function here is deterministic and free of
I/O / side effects (aside from reading environment in the budget
``from_env`` constructors).

Public names are re-exported from :mod:`latticeai.core.agent` so callers
keep importing from the original location.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple

from latticeai.core.agent_state import AgentState
from latticeai.core.file_generation import infer_file_target, infer_project_manifest


# ── action parsing ─────────────────────────────────────────────────────

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


# ── plan normalization ─────────────────────────────────────────────────

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


# ── learnings filter ───────────────────────────────────────────────────

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


# ── transcript shaping ─────────────────────────────────────────────────

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


def format_requirement_coverage(coverage: Dict[str, Any]) -> str:
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


def format_artifact_checklist(checklist: List[Dict[str, Any]]) -> str:
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


# ── budgets ────────────────────────────────────────────────────────────

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
