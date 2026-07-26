"""Plain-language explanation of an agent run's outcome (v9.9.6).

The loop already records *what* happened — :class:`~latticeai.core.agent_trace.LoopTrace`
counters, the transcript, the terminal state. What it never produced was a
sentence a person can read: why a run ended as ``NEEDS_REVIEW`` rather than
``DONE``, or how much the model struggled with the tool-call format before it
got there. Weak local models (Gemma-class) routinely need three format
repairs and one correction hint to finish a task that a large model does in
one pass; the counters showed that, the UI did not.

This module turns the machine-readable signals into one honest, deterministic
summary::

    {"code": "no_evidence",
     "headline": {"ko": "...", "en": "..."},
     "details": [{"ko": "...", "en": "..."}],
     "model_strain": {"level": "moderate", "parse_errors": 2, ...}}

Rules the rest of the system relies on:

* **Deterministic.** No model call, no clock, no I/O — the same run always
  explains the same way, so tests and the eval harness can assert on it.
* **Honest.** It never upgrades an outcome. A ``NEEDS_REVIEW`` explanation
  says the result was not verified; it never reads as a soft success.
* **Advisory.** Building an explanation must never fail a run — callers get a
  minimal payload rather than an exception.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

__all__ = ["explain_run", "STRAIN_LEVELS"]


STRAIN_LEVELS = ("none", "light", "moderate", "heavy")

# Repair names produced by ``extract_action_details`` / the artifact pipeline,
# grouped so the explanation says what the model got wrong, not which internal
# tolerance fired.
_REPAIR_GROUPS: Dict[str, tuple] = {
    "format": ("fence", "slice", "think_strip", "trailing_comma", "python_literal"),
    "plan": (
        "plan_not_object",
        "goal_defaulted",
        "steps_filtered",
        "estimated_steps_invalid",
        "manifest_steps",
        "manifest_rewrite",
        "heuristic_file_step",
    ),
    "artifact": ("artifact_sanitize", "artifact_repair"),
}


def _phrase(ko: str, en: str) -> Dict[str, str]:
    return {"ko": ko, "en": en}


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _steps(transcript: Any) -> List[Mapping[str, Any]]:
    if not isinstance(transcript, Sequence) or isinstance(transcript, (str, bytes)):
        return []
    return [step for step in transcript if isinstance(step, Mapping)]


def _last_verdict(steps: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    for step in reversed(steps):
        if step.get("state") == "VERIFYING":
            return step
    return None


def _repaired_artifacts(steps: Sequence[Mapping[str, Any]]) -> List[str]:
    """Paths whose content the artifact pipeline had to repair into a scaffold."""
    paths: List[str] = []
    for step in steps:
        meta = step.get("content_sanitize")
        if not isinstance(meta, Mapping) or not meta.get("repaired"):
            continue
        result = step.get("result") if isinstance(step.get("result"), Mapping) else {}
        args = step.get("args") if isinstance(step.get("args"), Mapping) else {}
        path = result.get("path") or args.get("path")
        if path and str(path) not in paths:
            paths.append(str(path))
    return paths


def _blocked_actions(steps: Sequence[Mapping[str, Any]]) -> List[str]:
    blocked: List[str] = []
    for step in steps:
        error = str(step.get("error") or "")
        if error.startswith("BLOCKED:") and step.get("action"):
            name = str(step["action"])
            if name not in blocked:
                blocked.append(name)
    return blocked


def _proposed_count(steps: Sequence[Mapping[str, Any]]) -> int:
    return sum(
        1
        for step in steps
        if isinstance(step.get("result"), Mapping) and step["result"].get("proposed")
    )


def _repair_totals(loop: Mapping[str, Any]) -> Dict[str, int]:
    repairs = loop.get("repairs")
    repairs = repairs if isinstance(repairs, Mapping) else {}
    totals = {group: 0 for group in _REPAIR_GROUPS}
    totals["other"] = 0
    for name, count in repairs.items():
        amount = _int(count)
        for group, members in _REPAIR_GROUPS.items():
            if name in members:
                totals[group] += amount
                break
        else:
            totals["other"] += amount
    return totals


def _strain_level(score: int) -> str:
    if score <= 0:
        return "none"
    if score <= 2:
        return "light"
    if score <= 5:
        return "moderate"
    return "heavy"


def _missing_files(steps: Sequence[Mapping[str, Any]]) -> List[str]:
    """Requested files the run never wrote (from the coverage check)."""
    for step in reversed(steps):
        coverage = step.get("requirement_coverage")
        if isinstance(coverage, Mapping):
            missing = coverage.get("missing_files")
            if isinstance(missing, Sequence) and not isinstance(missing, (str, bytes)):
                return [str(path) for path in missing if path]
    return []


def _outcome(state: str, steps: Sequence[Mapping[str, Any]]) -> str:
    """Terminal-state reason code — the *why*, not just the state name."""
    verdict_step = _last_verdict(steps)
    if state == "DONE":
        return "done"
    if state == "NEEDS_REVIEW":
        if _missing_files(steps):
            return "missing_files"
        if verdict_step is None:
            return "needs_review"
        if verdict_step.get("verdict") == "UNAVAILABLE":
            return "verifier_unavailable"
        if verdict_step.get("verdict") == "PASS" and not verdict_step.get("evidence"):
            return "no_evidence"
        if verdict_step.get("next_state") == "DONE":
            return "inconsistent_verdict"
        return "needs_review"
    if state == "FAILED":
        if any(step.get("state") == "ROLLBACK" for step in steps):
            return "rolled_back"
        for step in steps:
            if step.get("state") == "WAITING_APPROVAL" and (
                step.get("decision") == "blocked_pending_approval"
            ):
                return "approval_required"
        if verdict_step is not None and verdict_step.get("next_state") == "EXECUTING":
            return "retry_budget"
        return "failed"
    return "unknown"


_HEADLINES: Dict[str, Dict[str, str]] = {
    "done": _phrase(
        "요청한 작업을 끝냈고 검증도 통과했습니다.",
        "The task finished and passed verification.",
    ),
    "verifier_unavailable": _phrase(
        "작업은 실행됐지만 검증 모델의 답을 읽지 못해 '완료'로 처리하지 않았습니다.",
        "The work ran, but the reviewing model's answer could not be read, so it was not marked complete.",
    ),
    "no_evidence": _phrase(
        "검증자는 통과라고 했지만 실제로 실행된 기록이 없어 '완료'로 처리하지 않았습니다.",
        "The reviewer said PASS but there is no record of anything actually running, so it was not marked complete.",
    ),
    "inconsistent_verdict": _phrase(
        "검증 결과가 앞뒤가 맞지 않아 '완료'로 처리하지 않았습니다.",
        "The verification verdict contradicted itself, so it was not marked complete.",
    ),
    "missing_files": _phrase(
        "요청한 파일 중 일부가 만들어지지 않아 '완료'로 처리하지 않았습니다.",
        "Some requested files were never written, so it was not marked complete.",
    ),
    "needs_review": _phrase(
        "작업은 끝났지만 완료를 확인하지 못했습니다. 결과를 직접 확인해 주세요.",
        "The run ended but completion could not be confirmed — please check the result yourself.",
    ),
    "rolled_back": _phrase(
        "실행이 실패해서 바꾼 파일을 되돌렸습니다.",
        "Execution failed, so the changed files were rolled back.",
    ),
    "approval_required": _phrase(
        "승인이 필요한 도구가 있어 실행을 시작하지 못했습니다.",
        "The run never started because it needed approval for a gated tool.",
    ),
    "retry_budget": _phrase(
        "검증자가 계속 다시 하라고 해서 재시도 한도까지 갔고, 끝내 완료하지 못했습니다.",
        "The reviewer kept asking for another attempt until the retry budget ran out.",
    ),
    "failed": _phrase(
        "작업을 끝내지 못했습니다.",
        "The task did not complete.",
    ),
    "unknown": _phrase(
        "실행 상태를 확인하지 못했습니다.",
        "The run state could not be determined.",
    ),
}


_STRAIN_PHRASES: Dict[str, Dict[str, str]] = {
    "light": _phrase(
        "이 모델은 형식을 살짝 헷갈려 했지만 자동으로 정리했습니다.",
        "The model slipped on the reply format once or twice; it was cleaned up automatically.",
    ),
    "moderate": _phrase(
        "이 모델은 형식을 자주 틀려서 자동 보정이 여러 번 필요했습니다. 더 큰 모델을 쓰면 결과가 안정적입니다.",
        "The model needed several automatic format repairs. A larger model would be steadier.",
    ),
    "heavy": _phrase(
        "이 모델에게는 이 작업이 버거웠습니다 — 형식 오류와 보정이 많았습니다. 더 큰 모델로 다시 시도하는 것을 권합니다.",
        "This task strained the model — many format errors and repairs. Consider retrying with a larger model.",
    ),
}


def explain_run(
    *,
    state: Any,
    loop: Optional[Mapping[str, Any]] = None,
    transcript: Any = None,
    max_retry: Optional[int] = None,
) -> Dict[str, Any]:
    """Explain one finished agent run in plain language.

    ``state`` is the terminal :class:`~latticeai.core.agent.AgentState` value
    (or its ``.value`` string), ``loop`` a :meth:`LoopTrace.summary` payload,
    ``transcript`` the run transcript. Every argument is optional-tolerant:
    a partial run still produces a usable payload rather than raising.
    """
    state_name = str(getattr(state, "value", state) or "").upper()
    loop = loop if isinstance(loop, Mapping) else {}
    steps = _steps(transcript)

    code = _outcome(state_name, steps)
    parse_errors = _int(loop.get("parse_errors"))
    parse_recovered = _int(loop.get("parse_recovered"))
    corrections = _int(loop.get("corrections"))
    retries = _int(loop.get("retries"))
    repair_totals = _repair_totals(loop)
    repair_sum = sum(repair_totals.values())

    strain_score = parse_errors * 2 + corrections + retries + repair_totals["format"]
    strain = {
        "level": _strain_level(strain_score),
        "score": strain_score,
        "parse_errors": parse_errors,
        "parse_recovered": parse_recovered,
        "corrections": corrections,
        "retries": retries,
        "repairs": repair_totals,
        "repair_total": repair_sum,
    }

    details: List[Dict[str, str]] = []
    if parse_errors:
        details.append(_phrase(
            f"모델이 정해진 형식을 {parse_errors}번 벗어났고, 그중 {parse_recovered}번은 자동으로 복구했습니다.",
            f"The model broke the required reply format {parse_errors} time(s); "
            f"{parse_recovered} of those were recovered automatically.",
        ))
    if repair_totals["format"]:
        details.append(_phrase(
            f"모델 응답을 {repair_totals['format']}번 자동으로 다듬어 읽었습니다 (코드블록·따옴표 정리 등).",
            f"The model's reply needed {repair_totals['format']} automatic clean-up(s) "
            "(code fences, quoting, stray text).",
        ))
    if repair_totals["plan"]:
        details.append(_phrase(
            f"계획이 비어 있거나 요청한 파일을 빠뜨려서 {repair_totals['plan']}번 자동으로 보완했습니다.",
            f"The plan was empty or missed requested files and was repaired {repair_totals['plan']} time(s).",
        ))
    repaired_paths = _repaired_artifacts(steps)
    if repaired_paths:
        details.append(_phrase(
            "다음 파일은 모델 출력이 온전하지 않아 기본 뼈대로 대신 저장했습니다 — 내용을 꼭 확인하세요: "
            + ", ".join(repaired_paths[:4]),
            "These files were saved as a generated scaffold because the model output was "
            "not usable — check them: " + ", ".join(repaired_paths[:4]),
        ))
    if retries:
        details.append(_phrase(
            f"검증자가 {retries}번 다시 하라고 요청했습니다.",
            f"The reviewer asked for another attempt {retries} time(s).",
        ))
    proposed = _proposed_count(steps)
    if proposed:
        details.append(_phrase(
            f"기존 내용을 바꾸는 작업 {proposed}건은 바로 적용하지 않고 변경 제안으로 저장했습니다. 검토함에서 승인하면 적용됩니다.",
            f"{proposed} change(s) to existing content were staged as review proposals "
            "instead of applied — approve them in the review center.",
        ))
    blocked = _blocked_actions(steps)
    if blocked:
        details.append(_phrase(
            "다음 도구는 정책상 막혀서 실행하지 않았습니다: " + ", ".join(blocked[:4]),
            "These tools were blocked by policy and did not run: " + ", ".join(blocked[:4]),
        ))
    if code == "retry_budget" and max_retry:
        details.append(_phrase(
            f"재시도 한도({max_retry}회)에 도달했습니다.",
            f"The retry budget ({max_retry}) was exhausted.",
        ))
    missing_files = _missing_files(steps)
    if missing_files:
        details.append(_phrase(
            "만들어지지 않은 파일: " + ", ".join(missing_files[:4]),
            "Files that were never written: " + ", ".join(missing_files[:4]),
        ))
    strain_phrase = _STRAIN_PHRASES.get(strain["level"])
    if strain_phrase is not None:
        details.append(dict(strain_phrase))

    return {
        "code": code,
        "state": state_name,
        "ok": code == "done",
        "headline": dict(_HEADLINES.get(code, _HEADLINES["unknown"])),
        "details": details,
        "model_strain": strain,
        # Failure-learning surface (review 루프 §3): a structured "what to do
        # differently", carried into the next plan by the project session
        # instead of being lost with the run.
        "next_step": _next_step(
            code, missing_files=missing_files, blocked=blocked, strain=strain
        ),
    }


_NEXT_STEPS: Dict[str, Dict[str, str]] = {
    "verifier_unavailable": _phrase(
        "검증 모델을 더 큰 모델로 바꾸거나, 결과를 직접 확인한 뒤 다시 실행하세요.",
        "Switch the reviewing model to a larger one, or check the result yourself and re-run.",
    ),
    "no_evidence": _phrase(
        "요청을 더 구체적으로 (무엇을 어디에 만들지) 적어 다시 실행하세요.",
        "Re-run with a more concrete request — what to build and where.",
    ),
    "inconsistent_verdict": _phrase(
        "결과를 확인한 뒤, 남은 부분만 다시 요청하세요.",
        "Check the result, then re-request only what is still missing.",
    ),
    "approval_required": _phrase(
        "승인 흐름으로 다시 실행하거나, 승인이 필요 없는 범위로 요청을 줄이세요.",
        "Re-run through the approval flow, or narrow the request to steps that need no approval.",
    ),
    "rolled_back": _phrase(
        "실패 원인을 확인한 뒤, 더 작은 단위로 나눠 다시 요청하세요.",
        "Check what failed, then re-request in smaller steps.",
    ),
    "retry_budget": _phrase(
        "요청을 더 작은 단계로 나누거나 더 큰 모델로 다시 시도하세요.",
        "Split the request into smaller steps, or retry with a larger model.",
    ),
}


def _next_step(
    code: str,
    *,
    missing_files: Sequence[str],
    blocked: Sequence[str],
    strain: Mapping[str, Any],
) -> Optional[Dict[str, str]]:
    """One concrete "do this next", or None when the run needs nothing.

    Specific evidence beats a generic template: a named missing file or a
    blocked tool says more than "try again".
    """
    if code == "done" and str(strain.get("level")) in ("none", "light"):
        return None
    if missing_files:
        names = ", ".join(list(missing_files)[:4])
        return _phrase(
            f"다음 파일만 다시 만들어 달라고 요청하세요: {names}",
            f"Ask again for just these files: {names}",
        )
    if blocked:
        names = ", ".join(list(blocked)[:3])
        return _phrase(
            f"막힌 도구({names}) 없이 할 수 있는 범위로 요청을 바꾸거나, 승인 후 다시 실행하세요.",
            f"Rephrase the request to avoid the blocked tools ({names}), or approve them and re-run.",
        )
    specific = _NEXT_STEPS.get(code)
    if specific is not None:
        return dict(specific)
    if str(strain.get("level")) == "heavy":
        return _phrase(
            "더 큰 모델로 다시 시도하면 성공률이 올라갑니다.",
            "Retrying with a larger model will raise the success rate.",
        )
    return None
