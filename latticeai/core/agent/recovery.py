"""ROLLBACK and MEMORY — putting the workspace back, and keeping the lesson.

Recovery tries git first where the policy says the tool is git-rollbackable,
then the pre-write snapshot the executor captured, and otherwise reports
``mode="none"`` rather than claiming a recovery it did not perform. The memory
phase records what a *terminal* run taught: DONE runs record what worked,
FAILED and NEEDS_REVIEW runs record what went wrong, stored under the run's
actual terminal state and never a blanket "ok".
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from latticeai.core.agent_helpers import extract_action, filter_learnings
from latticeai.core.agent_state import AGENT_TERMINAL_STATES, AgentState

from ._contract import AgentCore as _Core
from .context import AgentRunContext


class _RecoveryMixin(_Core):
    """The ROLLBACK and MEMORY phases of :class:`SingleAgentRuntime`."""

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
