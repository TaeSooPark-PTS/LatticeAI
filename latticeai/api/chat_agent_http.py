"""HTTP adapter for the local chat agent runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.api.chat_contracts import AgentEvalRequest, AgentRequest, AgentResumeRequest
from latticeai.api.chat_helpers import _LANG_HINT, detect_language, workspace_scope_from_request
from latticeai.core.agent import AgentRunContext, AgentState, normalize_plan
from latticeai.core.run_store import (
    AgentRunStore,
    hash_approval_token,
    restore_run_context,
)
from latticeai.services.tool_dispatch import collect_artifacts, collect_created_files


class AgentHTTPController:
    def __init__(
        self,
        *,
        runtime: Any,
        model_router: Any,
        require_user: Any,
        require_admin: Any,
        enforce_rate_limit: Any,
        authenticated_identity: Any,
        write_workspace: Any,
        save_to_history: Any,
        workspace_store: Any,
        workspace_graph: Any,
        hooks: Any,
        execute_tool: Any,
        base_dir: Path,
        agent_root: Path,
        ensure_agent_root: Any,
        funnel_metrics: Any = None,
        run_store: Any = None,
    ) -> None:
        self.runtime = runtime
        self.model_router = model_router
        self.require_user = require_user
        self.require_admin = require_admin
        self.enforce_rate_limit = enforce_rate_limit
        self.authenticated_identity = authenticated_identity
        self.write_workspace = write_workspace
        self.save_to_history = save_to_history
        self.workspace_store = workspace_store
        self.workspace_graph = workspace_graph
        self.hooks = hooks
        self.execute_tool = execute_tool
        self.base_dir = Path(base_dir)
        self.agent_root = Path(agent_root)
        self.ensure_agent_root = ensure_agent_root
        self.funnel_metrics = funnel_metrics
        self._pending_lock = threading.Lock()
        # awaiting_approval runs: run_id -> paused run state + approval token.
        # Fail-closed: governed steps only ever execute after resume presents
        # the matching, unexpired token for this run (or via the legacy
        # explicit human-in-loop context flow above).
        self._approvals: Dict[str, Dict[str, Any]] = {}
        self._approval_ttl_seconds = 10 * 60
        # Durable side of the approval loop (review Wave 0.1): paused runs are
        # mirrored to disk so a valid token still resumes after a restart or
        # from another worker process. In-memory stays the fast path.
        self.run_store = run_store if run_store is not None else AgentRunStore(
            Path(base_dir) / "data" / "agent_runs"
        )
        try:
            self.run_store.sweep_expired()
        except Exception as exc:  # noqa: BLE001 — hygiene must not break startup
            logging.warning("agent run store sweep failed: %s", exc)
        self._background_tasks: set[asyncio.Task] = set()

    def register_routes(self, router: APIRouter) -> None:
        @router.post("/agent/eval")
        async def agent_eval(req: AgentEvalRequest, request: Request):
            return await self.eval(req, request)

        @router.post("/agent")
        async def agent(req: AgentRequest, request: Request):
            return await self.agent(req, request)

        @router.post("/agent/resume")
        async def agent_resume(req: AgentResumeRequest, request: Request):
            return await self.resume(req, request)

        @router.get("/agent/approvals")
        async def agent_approvals(request: Request):
            return self.pending_approvals(request)

    def _schedule_background_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def finish(done: asyncio.Task) -> None:
            self._background_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logging.warning("background chat task failed: %s", exc)

        task.add_done_callback(finish)

    async def eval(self, req: AgentEvalRequest, request: Request) -> Dict[str, Any]:
        """Run a skill's schema.json eval cases."""
        if self.require_admin is not None:
            self.require_admin(request)
        else:
            self.require_user(request)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", req.skill):
            raise HTTPException(status_code=400, detail="Invalid skill name.")
        skills_root = (self.base_dir / "skills").resolve()
        skill_dir = (skills_root / req.skill).resolve()
        if skill_dir.parent != skills_root:
            raise HTTPException(status_code=400, detail="Invalid skill path.")
        schema_path = skill_dir / "schema.json"
        if not schema_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Skill '{req.skill}' not found or missing schema.json",
            )

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        eval_cases = schema.get("evals", [])
        if req.case_id:
            eval_cases = [case for case in eval_cases if case.get("id") == req.case_id]
        if not eval_cases:
            return {
                "skill": req.skill,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "results": [],
                "message": "No eval cases defined in schema.json",
            }

        action_name = schema.get("action", req.skill)
        results = []
        for case in eval_cases:
            case_id = case.get("id", "?")
            try:
                case_input = case.get("input", {})
                result = dispatch_tool(
                    self.hooks,
                    action_name,
                    case_input,
                    lambda: self.execute_tool(action_name, case_input),
                    source="eval",
                )
                criteria = case.get("pass_criteria", "")
                if "success == true" in criteria:
                    passed = result.get("success") is True
                elif "success == false" in criteria:
                    passed = result.get("success") is False
                else:
                    passed = True
                results.append(
                    {
                        "id": case_id,
                        "description": case.get("description", ""),
                        "passed": passed,
                        "result": result,
                        "pass_criteria": criteria,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "id": case_id,
                        "description": case.get("description", ""),
                        "passed": False,
                        "error": str(exc),
                        "pass_criteria": case.get("pass_criteria", ""),
                    }
                )
        passed_count = sum(1 for result in results if result.get("passed") is True)
        return {
            "skill": req.skill,
            "action": action_name,
            "total": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
            "results": results,
        }

    async def agent(
        self,
        req: AgentRequest,
        request: Request,
        on_step: Any = None,
    ) -> Dict[str, Any]:
        """Plan and execute a natural-language local agent run.

        ``on_step`` (optional) is a per-run step observer — the live SSE
        route attaches one so the client sees progress while EXECUTING.
        """
        current_user = self.require_user(request)
        self.enforce_rate_limit(current_user, "agent")
        effective_email = self.authenticated_identity(current_user, req.user_email)
        header_workspace = workspace_scope_from_request(request)
        if req.workspace_id and header_workspace and req.workspace_id != header_workspace:
            raise HTTPException(
                status_code=403,
                detail="workspace_id must match X-Workspace-Id.",
            )
        req.workspace_id = self.write_workspace(
            req.workspace_id or header_workspace,
            current_user,
        )
        req.user_email = effective_email
        if not self.model_router.current_model_id:
            raise HTTPException(
                status_code=400,
                detail="No model loaded. Call /models/load first.",
            )

        self.ensure_agent_root()
        language_hint = _LANG_HINT[detect_language(req.message)]
        max_steps = max(1, min(req.max_steps, 50))
        max_retry = 3
        ctx = AgentRunContext()
        if on_step is not None:
            ctx.on_step = on_step
        ctx.executing_model = req.executing_model
        ctx.reviewing_model = req.reviewing_model
        ctx.state = AgentState.PLANNING
        ctx.state_history.append(ctx.state.value)
        await self.runtime.plan(
            ctx,
            req,
            language_hint,
            current_user,
            model_id=req.planning_model,
        )

        requirements_probe = getattr(self.runtime, "approval_requirements", None)

        if req.human_in_loop:
            # Deprecated explicit pause (L1 unification, 9.9.5): the legacy
            # ``human_in_loop`` flow now rides the same durable approval
            # store as ``awaiting_approval`` — one pause path, one resume
            # path, restart-safe. The legacy wire contract is preserved:
            # ``status="waiting_approval"`` + ``context_id`` (token-less,
            # same-user resume).
            requirements = (
                requirements_probe(ctx)
                if callable(requirements_probe)
                else {"requires_approval": True, "non_auto_steps": [], "plan_summary": ""}
            )
            return self._pause_for_approval(
                ctx, req, language_hint, current_user, requirements,
                legacy_context=True,
            )

        # Interactive approval: when the plan needs human approval, pause the
        # run as awaiting_approval (short-TTL token) instead of failing it.
        # ``getattr`` keeps injected fake runtimes without the preview method
        # on the historical fail-closed path.
        if callable(requirements_probe):
            requirements = requirements_probe(ctx)
            if requirements.get("requires_approval"):
                return self._pause_for_approval(
                    ctx, req, language_hint, current_user, requirements,
                )

        self.runtime.approve(ctx, current_user, approved_by_human=False)
        return await self._finish(
            ctx,
            req,
            language_hint,
            current_user,
            max_steps,
            max_retry,
        )

    def _pause_for_approval(
        self,
        ctx: AgentRunContext,
        req: AgentRequest,
        language_hint: str,
        current_user: str,
        requirements: Dict[str, Any],
        *,
        legacy_context: bool = False,
    ) -> Dict[str, Any]:
        """Park a plan that needs approval and hand the user a resume token.

        ``legacy_context=True`` marks a deprecated ``human_in_loop`` pause:
        same durable storage, but the response speaks the historical
        ``waiting_approval``/``context_id`` contract and resume may present
        the ``context_id`` instead of the approval token (same user only).
        """
        run_id = secrets.token_urlsafe(16)
        approval_token = secrets.token_urlsafe(32)
        now_monotonic = time.monotonic()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=self._approval_ttl_seconds)
        ).isoformat(timespec="seconds")
        with self._pending_lock:
            self._purge_expired_approvals_locked(now_monotonic)
            self._approvals[run_id] = {
                "ctx": ctx,
                "req": req,
                "language_hint": language_hint,
                "user": current_user,
                "token": approval_token,
                "expires_monotonic": now_monotonic + self._approval_ttl_seconds,
                "expires_at": expires_at,
                "legacy_context": legacy_context,
            }
        # Durable mirror: a restart between pause and resume must not orphan
        # the run. Best-effort — the in-memory pause still answers on failure.
        try:
            self.run_store.save(
                run_id,
                ctx=ctx,
                req_payload=req.model_dump(),
                language_hint=language_hint,
                user=current_user,
                token=approval_token,
                expires_epoch=time.time() + self._approval_ttl_seconds,
                expires_at=expires_at,
                legacy_context=legacy_context,
            )
        except Exception as exc:  # noqa: BLE001
            logging.warning("agent run store persist failed: %s", exc)
        if self.funnel_metrics is not None:
            try:
                self.funnel_metrics.increment("approval_pauses")
            except Exception as exc:  # noqa: BLE001 — advisory only
                logging.warning("funnel metrics increment failed: %s", exc)
        ctx.state_history.append(AgentState.WAITING_APPROVAL.value)
        message = (
            "이 작업에는 승인이 필요한 단계가 있어 실행을 잠시 멈췄습니다. "
            "계획을 확인한 뒤 승인하면 이어서 실행합니다."
        )
        payload = {
            "status": "waiting_approval" if legacy_context else "awaiting_approval",
            "run_id": run_id,
            "approval": {
                "token": approval_token,
                "expires_at": expires_at,
                "plan_summary": requirements.get("plan_summary", ""),
            },
            "response": message,
            "plan": ctx.plan,
            "steps": ctx.transcript,
            "state_history": ctx.state_history,
            "final_state": AgentState.WAITING_APPROVAL.value,
            "non_auto_steps": requirements.get("non_auto_steps", []),
            "planning_model": req.planning_model or self.model_router.current_model_id,
            "executing_model": req.executing_model or self.model_router.current_model_id,
            "reviewing_model": req.reviewing_model or self.model_router.current_model_id,
            "loop": ctx.trace.summary(),
        }
        if legacy_context:
            # Historical wire field — the run id doubles as the context id.
            payload["context_id"] = run_id
        return payload

    def _purge_expired_approvals_locked(self, now_monotonic: float) -> None:
        for run_id, entry in list(self._approvals.items()):
            if now_monotonic >= entry["expires_monotonic"]:
                self._approvals.pop(run_id, None)
                try:
                    self.run_store.delete(run_id)
                except Exception:  # noqa: BLE001 — hygiene only
                    pass

    def pending_approvals(self, request: Request) -> Dict[str, Any]:
        """Unexpired paused runs for the current user (memory ∪ disk).

        Lets the UI re-surface an approval card after a reload/restart instead
        of the run silently vanishing.
        """
        current_user = self.require_user(request)
        now_monotonic = time.monotonic()
        pending: Dict[str, Dict[str, Any]] = {}
        with self._pending_lock:
            for run_id, entry in self._approvals.items():
                if entry["user"] != current_user:
                    continue
                if now_monotonic >= entry["expires_monotonic"]:
                    continue
                plan = getattr(entry["ctx"], "plan", {}) or {}
                pending[run_id] = {
                    "run_id": run_id,
                    "goal": str(plan.get("goal") or "")[:200],
                    "expires_at": entry["expires_at"],
                }
        try:
            for summary in self.run_store.pending_summaries(current_user):
                run_id = summary.get("run_id")
                if run_id and run_id not in pending:
                    pending[run_id] = {
                        "run_id": run_id,
                        "goal": summary.get("goal") or "",
                        "expires_at": summary.get("expires_at"),
                    }
        except Exception as exc:  # noqa: BLE001 — disk listing is best-effort
            logging.warning("agent run store listing failed: %s", exc)
        return {"pending": sorted(pending.values(), key=lambda p: p["run_id"])}

    def _restore_persisted_approval(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Rebuild an approval entry from disk after a restart.

        Returns an entry shaped like the in-memory ones but carrying
        ``token_hash`` instead of the plaintext token. Expired records are
        deleted and reported via the same 410 contract as the memory path.
        """
        try:
            record = self.run_store.load(run_id)
        except Exception as exc:  # noqa: BLE001 — load error == not found
            logging.warning("agent run store load failed: %s", exc)
            return None
        if record is None:
            return None
        req_payload = record.get("req") or {}
        if time.time() >= float(record.get("expires_epoch") or 0):
            try:
                self.run_store.delete(run_id)
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=410,
                detail={
                    "error": "approval_expired",
                    "message": "Approval token expired. Start a new request.",
                    "replan": {"message": str(req_payload.get("message") or "")},
                },
            )
        try:
            restored_req = AgentRequest(**req_payload)
        except Exception as exc:  # noqa: BLE001 — unreconstructable == not found
            logging.warning("agent run store request restore failed: %s", exc)
            return None
        remaining = max(1.0, float(record["expires_epoch"]) - time.time())
        return {
            "ctx": restore_run_context(record.get("ctx") or {}),
            "req": restored_req,
            "language_hint": str(record.get("language_hint") or "English"),
            "user": record.get("user"),
            "token_hash": str(record.get("token_hash") or ""),
            "expires_monotonic": time.monotonic() + remaining,
            "expires_at": record.get("expires_at"),
            "legacy_context": bool(record.get("legacy_context")),
        }

    @staticmethod
    def _token_matches(entry: Dict[str, Any], supplied: str) -> bool:
        """Constant-time token check for both plaintext and hashed entries."""
        if not supplied:
            return False
        stored = entry.get("token")
        if stored is not None:
            return secrets.compare_digest(str(stored), supplied)
        stored_hash = str(entry.get("token_hash") or "")
        return bool(stored_hash) and secrets.compare_digest(
            stored_hash, hash_approval_token(supplied)
        )

    async def _finish(
        self,
        ctx: AgentRunContext,
        req: AgentRequest,
        language_hint: str,
        current_user: str,
        max_steps: int,
        max_retry: int,
    ) -> Dict[str, Any]:
        await self.runtime.run_to_completion(
            ctx,
            req,
            language_hint,
            current_user,
            max_steps,
            max_retry,
        )
        self._schedule_background_task(self.runtime.memory_update(ctx, req, current_user))

        message = ctx.final_message or "작업을 완료했습니다."
        history_user = {
            "user_email": req.user_email or current_user or None,
            "user_nickname": req.user_nickname,
        }
        await asyncio.to_thread(
            self.save_to_history,
            "user",
            req.message,
            **history_user,
            source=req.source or "web",
            conversation_id=req.conversation_id,
            workspace_id=req.workspace_id,
        )
        await asyncio.to_thread(
            self.save_to_history,
            "assistant",
            message,
            **history_user,
            source=req.source or "web",
            conversation_id=req.conversation_id,
            workspace_id=req.workspace_id,
        )
        try:
            self.workspace_store.record_agent_run(
                agent_id="agent:executor",
                status="ok" if ctx.state == AgentState.DONE else "failed",
                input_text=req.message,
                output_text=message,
                user_email=current_user or None,
                workspace_id=req.workspace_id,
                mode="llm",
                timeline=ctx.transcript,
                relationships=["agent:planner", "agent:reviewer"],
                graph=self.workspace_graph(),
            )
        except Exception as exc:
            logging.warning("workspace agent run record failed: %s", exc)
        if self.funnel_metrics is not None:
            # UX funnel (backlog #16): every completed agent run counts, and
            # NEEDS_REVIEW terminals feed the needs_review_rate the review
            # gates watch. Advisory only — a metrics failure never fails chat.
            try:
                self.funnel_metrics.increment("agent_runs")
                if ctx.state == AgentState.NEEDS_REVIEW:
                    self.funnel_metrics.increment("needs_review_runs")
            except Exception as exc:  # noqa: BLE001
                logging.warning("funnel metrics increment failed: %s", exc)
        return {
            "status": "ok" if ctx.state == AgentState.DONE else "failed",
            "response": message,
            "workspace": str(self.agent_root),
            "steps": ctx.transcript,
            "state_history": ctx.state_history,
            "final_state": ctx.state.value,
            "created_files": collect_created_files(ctx.transcript),
            "artifacts": collect_artifacts(ctx.transcript),
            "loop": ctx.trace.summary(),
        }

    async def resume(
        self,
        req: AgentResumeRequest,
        request: Request,
    ) -> Dict[str, Any]:
        """Resume a paused agent after human approval of the plan.

        Two entry modes share this endpoint:

        * ``run_id`` + ``approval_token`` — an ``awaiting_approval`` run
          (token-validated, short TTL, bound to the pausing user);
        * ``context_id`` — the legacy explicit human-in-loop pause.
        """
        current_user = self.require_user(request)
        if req.run_id:
            return await self._resume_approval(req, current_user)
        if not req.context_id:
            raise HTTPException(
                status_code=400,
                detail="run_id (with approval_token) or context_id is required.",
            )
        # Deprecated context_id resume (L1 unification, 9.9.5): the legacy
        # pause lives in the same durable approval store now. Token-less
        # resume stays allowed for it — bound to the pausing user, and only
        # for entries that were created through the legacy pause.
        now_monotonic = time.monotonic()
        with self._pending_lock:
            entry = self._approvals.get(req.context_id)
            if entry is None:
                try:
                    entry = self._restore_persisted_approval(req.context_id)
                except HTTPException as exc:
                    if exc.status_code == 410:
                        # Legacy contract never had a distinct expiry answer.
                        raise HTTPException(
                            status_code=404,
                            detail="Agent context not found or expired. Start a new request.",
                        ) from exc
                    raise
            if entry is None or not entry.get("legacy_context"):
                raise HTTPException(
                    status_code=404,
                    detail="Agent context not found or expired. Start a new request.",
                )
            if entry["user"] != current_user:
                raise HTTPException(
                    status_code=403,
                    detail="Agent context belongs to another user.",
                )
            if now_monotonic >= entry["expires_monotonic"]:
                self._approvals.pop(req.context_id, None)
                try:
                    self.run_store.delete(req.context_id)
                except Exception:  # noqa: BLE001
                    pass
                raise HTTPException(
                    status_code=404,
                    detail="Agent context not found or expired. Start a new request.",
                )
            self._approvals.pop(req.context_id, None)
            try:
                self.run_store.delete(req.context_id)
            except Exception as exc:  # noqa: BLE001
                logging.warning("agent run store delete failed: %s", exc)

        ctx = entry["ctx"]
        original_request = entry["req"]
        language_hint = entry["language_hint"]
        if not req.approved:
            return {"status": "cancelled", "response": "사용자가 계획을 취소했습니다."}
        if req.modified_plan:
            plan, plan_fixes = normalize_plan(req.modified_plan, original_request.message)
            ctx.plan = plan
            ctx.transcript.append({
                "state": AgentState.WAITING_APPROVAL.value,
                "edited_plan": True,
                **({"plan_fixes": plan_fixes} if plan_fixes else {}),
            })
        ctx.executing_model = req.executing_model or ctx.executing_model
        ctx.reviewing_model = req.reviewing_model or ctx.reviewing_model
        # The authenticated owner of this pending context explicitly approved
        # the plan — record it as a human approval so approval-gated steps can
        # actually run instead of terminating the run as FAILED.
        self.runtime.approve(ctx, current_user, approved_by_human=True)
        return await self._finish(
            ctx,
            original_request,
            language_hint,
            current_user,
            max(1, min(original_request.max_steps, 50)),
            3,
        )

    async def _resume_approval(
        self,
        req: AgentResumeRequest,
        current_user: str,
    ) -> Dict[str, Any]:
        """Validate an awaiting_approval token, then continue or cancel the run."""
        now_monotonic = time.monotonic()
        with self._pending_lock:
            entry = self._approvals.get(req.run_id or "")
            if entry is None:
                # Restart survival (review Wave 0.1): the in-memory dict is
                # gone but the durable record may still be valid.
                entry = self._restore_persisted_approval(req.run_id or "")
            if entry is None:
                raise HTTPException(
                    status_code=404,
                    detail="Agent run not found. It may have expired — start a new request.",
                )
            if entry["user"] != current_user:
                raise HTTPException(
                    status_code=403,
                    detail="Agent run belongs to another user.",
                )
            if now_monotonic >= entry["expires_monotonic"]:
                self._approvals.pop(req.run_id, None)
                try:
                    self.run_store.delete(req.run_id or "")
                except Exception:  # noqa: BLE001
                    pass
                raise HTTPException(
                    status_code=410,
                    detail={
                        "error": "approval_expired",
                        "message": "Approval token expired. Start a new request.",
                        "replan": {"message": getattr(entry.get("req"), "message", "")},
                    },
                )
            if not self._token_matches(entry, req.approval_token or ""):
                raise HTTPException(
                    status_code=403,
                    detail="Invalid approval token for this run.",
                )
            # Token validated — the pending run is consumed either way.
            self._approvals.pop(req.run_id, None)
            try:
                self.run_store.delete(req.run_id or "")
            except Exception as exc:  # noqa: BLE001 — consumption must proceed
                logging.warning("agent run store delete failed: %s", exc)
            self._purge_expired_approvals_locked(now_monotonic)

        ctx: AgentRunContext = entry["ctx"]
        original_request: AgentRequest = entry["req"]
        language_hint: str = entry["language_hint"]

        if self.funnel_metrics is not None:
            # approval_resume_rate = resumes / pauses (review §4.3): counted on
            # every token-valid resume decision, approve and deny alike.
            try:
                self.funnel_metrics.increment("approval_resumes")
            except Exception as exc:  # noqa: BLE001 — advisory only
                logging.warning("funnel metrics increment failed: %s", exc)

        approved = req.approve if req.approve is not None else req.approved
        if not approved:
            message = "사용자가 계획을 취소했습니다."
            try:
                self.workspace_store.record_agent_run(
                    agent_id="agent:executor",
                    status="cancelled",
                    input_text=original_request.message,
                    output_text=message,
                    user_email=current_user or None,
                    workspace_id=original_request.workspace_id,
                    mode="llm",
                    timeline=ctx.transcript,
                    relationships=["agent:planner", "agent:reviewer"],
                    graph=self.workspace_graph(),
                )
            except Exception as exc:
                logging.warning("workspace agent run record failed: %s", exc)
            return {
                "status": "cancelled",
                "run_id": req.run_id,
                "response": message,
            }

        edited_plan = req.edited_plan or req.modified_plan
        if edited_plan:
            plan, plan_fixes = normalize_plan(edited_plan, original_request.message)
            ctx.plan = plan
            ctx.transcript.append({
                "state": AgentState.WAITING_APPROVAL.value,
                "edited_plan": True,
                **({"plan_fixes": plan_fixes} if plan_fixes else {}),
            })
        ctx.executing_model = req.executing_model or ctx.executing_model
        ctx.reviewing_model = req.reviewing_model or ctx.reviewing_model
        self.runtime.approve(ctx, current_user, approved_by_human=True)
        return await self._finish(
            ctx,
            original_request,
            language_hint,
            current_user,
            max(1, min(original_request.max_steps, 50)),
            3,
        )


__all__ = ["AgentHTTPController"]
