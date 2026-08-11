"""VERIFY — the critic's verdict, and the facts that outrank it.

Fail-closed by construction. A critic whose output cannot be parsed (after one
strict repair retry) never fabricates a PASS; a PASS over a transcript with no
execution evidence is not a completion; and a PASS that leaves a *requested
file* unwritten is a fact, not a judgement, so it is enforced rather than
merely reported back to the critic.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from latticeai.core.agent_helpers import (
    _truncate_strings,
    artifact_checklist,
    extract_action_details,
    format_artifact_checklist,
    format_requirement_coverage,
    requirement_coverage,
)
from latticeai.core.agent_state import AgentState

from ._contract import AgentCore as _Core
from .context import AgentRunContext


class _VerificationMixin(_Core):
    """The VERIFY phase of :class:`SingleAgentRuntime`."""

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
            f"\n\n{format_artifact_checklist(checklist)}" if checklist else ""
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
            f"{format_requirement_coverage(coverage)}\n\n"
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
