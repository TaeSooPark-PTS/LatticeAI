"""HTTP adapter for the local chat agent runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.api.chat_contracts import AgentEvalRequest, AgentRequest, AgentResumeRequest
from latticeai.api.chat_helpers import _LANG_HINT, detect_language, workspace_scope_from_request
from latticeai.core.agent import AgentRunContext, AgentState
from latticeai.services.tool_dispatch import collect_created_files


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
        self._pending: Dict[str, tuple] = {}
        self._pending_lock = threading.Lock()
        self._pending_ttl_seconds = 15 * 60
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

    async def agent(self, req: AgentRequest, request: Request) -> Dict[str, Any]:
        """Plan and execute a natural-language local agent run."""
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

        if req.human_in_loop:
            context_id = secrets.token_urlsafe(16)
            with self._pending_lock:
                self._pending[context_id] = (
                    ctx,
                    req,
                    language_hint,
                    current_user,
                    time.monotonic(),
                )
            return {
                "status": "waiting_approval",
                "context_id": context_id,
                "plan": ctx.plan,
                "steps": ctx.transcript,
                "state_history": ctx.state_history,
                "planning_model": req.planning_model or self.model_router.current_model_id,
                "executing_model": req.executing_model or self.model_router.current_model_id,
                "reviewing_model": req.reviewing_model or self.model_router.current_model_id,
                "loop": ctx.trace.summary(),
            }

        self.runtime.approve(ctx, current_user, approved_by_human=False)
        return await self._finish(
            ctx,
            req,
            language_hint,
            current_user,
            max_steps,
            max_retry,
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
        return {
            "status": "ok" if ctx.state == AgentState.DONE else "failed",
            "response": message,
            "workspace": str(self.agent_root),
            "steps": ctx.transcript,
            "state_history": ctx.state_history,
            "final_state": ctx.state.value,
            "created_files": collect_created_files(ctx.transcript),
            "loop": ctx.trace.summary(),
        }

    async def resume(
        self,
        req: AgentResumeRequest,
        request: Request,
    ) -> Dict[str, Any]:
        """Resume a paused agent after human approval of the plan."""
        current_user = self.require_user(request)
        with self._pending_lock:
            now = time.monotonic()
            for context_id, pending in list(self._pending.items()):
                if now - pending[4] >= self._pending_ttl_seconds:
                    self._pending.pop(context_id, None)
            entry = self._pending.get(req.context_id)
            if entry and entry[3] == current_user:
                self._pending.pop(req.context_id, None)
        if not entry:
            raise HTTPException(
                status_code=404,
                detail="Agent context not found or expired. Start a new request.",
            )

        ctx, original_request, language_hint, original_user, _created_at = entry
        if original_user != current_user:
            raise HTTPException(
                status_code=403,
                detail="Agent context belongs to another user.",
            )
        if not req.approved:
            return {"status": "cancelled", "response": "사용자가 계획을 취소했습니다."}
        if req.modified_plan:
            ctx.plan = req.modified_plan
            ctx.transcript[-1].update(ctx.plan)
        ctx.executing_model = req.executing_model or ctx.executing_model
        ctx.reviewing_model = req.reviewing_model or ctx.reviewing_model
        self.runtime.approve(ctx, current_user)
        return await self._finish(
            ctx,
            original_request,
            language_hint,
            current_user,
            max(1, min(original_request.max_steps, 50)),
            3,
        )


__all__ = ["AgentHTTPController"]
