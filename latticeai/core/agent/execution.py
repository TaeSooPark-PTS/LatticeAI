"""EXECUTE — one tool call at a time, through every gate, on the record.

The longest phase and the only one that changes anything, so it is also where
all the governance lives: central change classification (additive creates run,
mutations become review proposals), the mode-invariant hard denials, the
fail-closed overwrite guard, and the shared pre_tool/post_tool lifecycle. The
direct-path fallback at the bottom is the escape hatch for a model too small to
hold the tool-call protocol — it writes the planner's own files through the
same validated pipeline the chat path uses, and never fabricates evidence.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple

from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.core.agent_helpers import (
    compact_transcript,
    extract_action_details,
    files_written,
)
from latticeai.core.agent_permission import block_reason_for_tool
from latticeai.core.agent_profiles import AgentProfile
from latticeai.core.agent_prompts import executor_prompt_for
from latticeai.core.agent_state import AgentState
from latticeai.core.file_generation import (
    generate_file_content,
    infer_file_target,
    sanitize_write_content,
)
from latticeai.core.permission_mode import is_circuit_breaker, should_stage_proposal
from latticeai.core.tool_governor import classify_tool_call
from latticeai.core.tool_registry import SCOPED_KNOWLEDGE_TOOLS
from latticeai.tools import ToolError

from ._contract import AgentCore as _Core
from .context import AgentRunContext


class _ExecutionMixin(_Core):
    """The EXECUTE phase of :class:`SingleAgentRuntime`."""

    # ── EXECUTE ──────────────────────────────────────────────────────
    async def execute(
        self, ctx: AgentRunContext, req: Any, lang_hint: str,
        current_user: str, max_steps: int, model_id: Optional[str] = None,
    ) -> None:
        """EXECUTE: Executor role calls tools one at a time until final or budget exhausted."""
        d = self.deps
        profile = self.profile_for(model_id)
        exec_count = sum(1 for s in ctx.transcript if s.get("state") == AgentState.EXECUTING.value)
        budget = max(1, max_steps - exec_count)
        parse_failures = 0

        for _ in range(budget):
            request_workspace = getattr(req, "workspace_id", None)
            context = self._executor_context(
                ctx, req, lang_hint, current_user, request_workspace, profile=profile
            )
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
                if self._note_parse_failure(ctx, raw, exc, parse_failures, profile):
                    # Direct-path fallback (v9.9.7): a small model that cannot
                    # hold the tool-call protocol can still write a file. Run
                    # the plan's own file steps without asking for any JSON.
                    if profile.direct_path_fallback and await self._direct_file_path(
                        ctx, req, current_user, model_id
                    ):
                        ctx.state = AgentState.VERIFYING
                        return
                    break
                continue

            name     = str(action.get("action") or "")
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

    def _self_model_summary(
        self, ctx: AgentRunContext, current_user: str, request_workspace: Optional[str]
    ) -> str:
        """What this Brain knows about its owner, resolved once per run.

        The port may be a plain string or a resolver taking scope kwargs — the
        same two shapes ``permission_mode`` accepts, so there is one convention
        for "a value or a way to get one". Anything that goes wrong yields an
        empty summary: prompt assembly must not fail because a profile could
        not be read, and an empty summary is byte-identical to having no port
        at all.
        """
        if ctx.self_model_summary is not None:
            return ctx.self_model_summary
        source = self.deps.self_model_summary
        summary = ""
        if source is not None:
            try:
                if callable(source):
                    try:
                        summary = source(
                            user_email=current_user or None,
                            workspace_id=request_workspace,
                        )
                    except TypeError:
                        # A resolver that takes no scope arguments is allowed.
                        summary = source()
                else:
                    summary = source
            except Exception:  # noqa: BLE001 — a profile is never worth a failed run
                logging.debug("agent: self-model summary unavailable", exc_info=True)
                summary = ""
        ctx.self_model_summary = str(summary or "").strip()
        return ctx.self_model_summary

    def _executor_context(
        self, ctx: AgentRunContext, req: Any, lang_hint: str,
        current_user: str, request_workspace: Optional[str],
        profile: Optional[AgentProfile] = None,
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
        # A small model drowns in a long transcript far sooner than a large
        # one, so the profile may narrow the window (v9.9.7).
        window = min(budget.window, profile.transcript_window) if profile else budget.window
        bounded_transcript = compact_transcript(
            ctx.transcript,
            window=window,
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
            # v11.1.0: the executor prompt carries profile-aware file-writing
            # hints, because "wrote nothing at all" was the weak-model failure
            # mode the loop could not repair after the fact.
            f"{executor_prompt_for(d.executor_prompt, profile=profile, self_model_summary=self._self_model_summary(ctx, current_user, request_workspace))}\n\n"
            f"[LANGUAGE HINT: {lang_hint}]\n"
            f"Workspace root: {d.agent_root}{self._project_block(ctx)}\n\n"
            f"PLAN:\n{json.dumps(ctx.plan, ensure_ascii=False)}{written_hint}\n\n"
            f"Recent conversation:\n{recent_conversation}\n\n"
            f"User request: {req.message}{corrections_hint}\n\n"
            f"Execution transcript:\n{json.dumps(bounded_transcript, ensure_ascii=False, indent=2)}"
        )

    def _note_parse_failure(
        self, ctx: AgentRunContext, raw: Any, exc: ValueError, parse_failures: int,
        profile: Optional[AgentProfile] = None,
    ) -> bool:
        """Record one executor parse slip; True when the run should stop retrying."""
        profile = profile or self.profile_for(None)
        ctx.transcript.append({
            "state": AgentState.EXECUTING.value, "action": "parse_error",
            "raw": str(raw)[:400], "error": str(exc),
        })
        if parse_failures >= profile.parse_failure_budget:
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
        if parse_failures >= profile.escalate_after:
            # Escalate: name the valid tools so the model stops
            # inventing action names or prose. The compact profile escalates
            # a slip earlier — a small model needs the list sooner.
            valid = ", ".join(sorted(self.deps.tool_governance.keys()))
            hint = (
                f"{hint} Valid action values are: {valid}, final. "
                'Use {"action": "final", "message": "..."} to finish.'
            )
        if hint not in ctx.corrections:
            ctx.corrections.append(hint)
            ctx.trace.correction("execute", hint=hint)
        return False

    async def _direct_file_path(
        self, ctx: AgentRunContext, req: Any, current_user: str,
        model_id: Optional[str],
    ) -> bool:
        """Write the plan's file steps without asking the model for JSON (v9.9.7).

        The compact profile's escape hatch. A 1–4B local model that cannot hold
        the tool-call protocol can still write a file, so when JSON tool calls
        are exhausted the loop drops the protocol entirely: it takes the paths
        the *planner* already chose and asks only for file content in plain
        text, through the same validated
        :func:`~latticeai.core.file_generation.generate_file_content` pipeline
        the direct chat path uses.

        Returns True when at least one file was actually written. Honest
        failure modes: no planned paths, a governor that stages the write as a
        proposal, or a tool error all return False and leave the run to end as
        it would have — this never fabricates evidence.
        """
        d = self.deps
        planned: List[str] = []
        for step in ctx.plan.get("steps") or []:
            if not isinstance(step, dict) or step.get("action") not in d.file_create_actions:
                continue
            path = str((step.get("args") or {}).get("path") or "").strip()
            if path and path not in planned:
                planned.append(path)
        if not planned:
            inferred = infer_file_target(getattr(req, "message", "") or "")
            if inferred:
                planned = [inferred]
        if not planned:
            return False

        goal = str(ctx.plan.get("goal") or getattr(req, "message", "") or "")

        async def _generate(context: str) -> Any:
            return await d.generate_as(
                model_id,
                message="Write the file content.",
                context=context,
                max_tokens=self.phase_budgets.execute_tokens,
                temperature=0.2,
            )

        wrote = False
        for path in planned[:6]:
            try:
                content, meta = await generate_file_content(
                    _generate,
                    target_path=path,
                    user_request=goal,
                    bundle_files=planned if len(planned) > 1 else None,
                )
            except Exception as exc:  # noqa: BLE001 — fallback must not raise
                logging.warning("direct file path generation failed for %s: %s", path, exc)
                continue
            ctx.trace.llm_call("execute", model=model_id)
            ctx.trace.repair("execute", repairs=["direct_path_fallback"])
            args = {"path": path, "content": content}
            policy = d.policy_for("write_file", args)
            risk = d.risk_level(policy)
            before = len(ctx.transcript)
            self._dispatch_step(ctx, "write_file", "direct path fallback", args, policy, risk, current_user)
            last = ctx.transcript[-1] if len(ctx.transcript) > before else {}
            if isinstance(last.get("result"), dict) and not last["result"].get("proposed"):
                wrote = True
                last["direct_path"] = True
                last["generation"] = {"repaired": bool(meta.get("repaired"))}
        if wrote:
            ctx.trace.decision("execute", decision="direct_path_fallback", files=len(planned))
            self._emit_step(ctx, "execute", "direct_path", files=len(planned))
            ctx.final_message = (
                "도구 호출 형식을 계속 벗어나서, 계획에 있던 파일을 직접 생성했습니다. "
                "내용을 확인해 주세요."
            )
        return wrote

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
        policy: Mapping[str, Any], risk: str, current_user: str, request_workspace: Optional[str],
        conversation_id: Optional[str] = None,
    ) -> Tuple[bool, bool]:
        """Central change-class governance: create-new runs with minimal
        friction, change/delete-existing becomes a review proposal.

        Returns ``(proposed, governor_allows_additive)``: ``proposed`` means the
        step was staged as a proposal (skip execution); ``allows_additive`` lets
        an additive create pass the classic approval gate.

        Under a mode that does not stage proposals (``trusted`` / ``bypass``)
        the decision is made *before* the governor is consulted, because
        ``review`` persists a proposal as a side effect — reviewing first and
        discarding the verdict afterwards would apply the change *and* leave an
        orphan proposal pending in the Review Center.
        """
        d = self.deps
        if d.change_governor is None:
            return False, False

        mode = self.resolve_permission_mode(
            ctx, user_email=current_user, workspace_id=request_workspace,
        )
        if not should_stage_proposal(mode, proposal_required=True):
            if name not in self._governed_tools():
                return False, False
            if policy.get("destructive") or policy.get("risk") == "destructive":
                # Let the destructive gate downstream own the block + transcript.
                return False, False
            d.audit(
                "agent_change_auto_applied",
                user_email=current_user,
                workspace_id=request_workspace,
                action=name,
                path=str(args.get("path") or "") or None,
                permission_mode=mode.value,
                note="permission mode auto-applies mutation with audit",
            )
            return False, True

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
        policy: Mapping[str, Any], risk: str, current_user: str, governor_allows_additive: bool,
    ) -> bool:
        """Destructive / circuit-breaker / fail-closed-overwrite / approval gates.

        Returns True when the step was blocked. The active permission mode can
        widen what runs without an extra approval prompt, but never widens a
        circuit breaker, the destructive gate, or the overwrite check.
        """
        d = self.deps
        mode = self.resolve_permission_mode(
            ctx,
            user_email=current_user,
            workspace_id=getattr(req, "workspace_id", None),
        )
        # Hard denials first — mode-invariant. A circuit breaker (root/home
        # paths, `rm -rf /` style commands) and a destructive policy are both
        # audited as ``blocked`` with the reason that actually fired, rather
        # than being flattened into the approval path.
        breaker = is_circuit_breaker(name, policy, args)
        hard_deny = breaker or (
            "destructive policy"
            if policy["risk"] == "destructive" or policy.get("destructive")
            else None
        )
        if hard_deny:
            error = (
                f"BLOCKED: destructive action '{name}' not permitted in agent mode."
                if hard_deny == "destructive policy"
                else f"BLOCKED: {hard_deny}"
            )
            ctx.trace.tool("execute", name=name, outcome="blocked_destructive", risk=risk)
            self._emit_step(ctx, "execute", "blocked", action=name, reason="destructive")
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts, "args": args, "risk": risk,
                "governance": dict(policy),
                "permission_mode": mode.value,
                "error": error,
            })
            d.audit(
                "agent_blocked", user_email=current_user, source=getattr(req, "source", None) or "agent",
                action=name, reason="destructive", governance=dict(policy),
            )
            return True

        # Fail-closed overwrite guard — mode-invariant, like the two above.
        # A call that rewrites existing content but cannot be staged as a
        # reviewable proposal (binary document creators, home-sandbox writes)
        # has no safe apply path in ANY mode: trusted/bypass skip the approval
        # *prompt*, they never remove the existence check. Without this the
        # loop silently overwrote files that the HTTP surface refuses with 409
        # (``ToolDispatchService.enforce_policy``).
        overwrite = classify_tool_call(
            name, args, policy=dict(policy),
            path_exists=lambda candidate: self._governed_path_exists(name, candidate),
        )
        if overwrite.get("fail_closed"):
            target = str(args.get("path") or args.get("filename") or "")
            error = (
                f"NEEDS_REVIEW: '{name}' 은(는) 이미 있는 파일 '{target}' 을(를) 덮어씁니다. "
                "이 도구의 변경은 검토 가능한 제안으로 만들 수 없어 실행하지 않았습니다. "
                "새 파일 이름으로 만들거나 write_file/edit_file 로 수정하세요."
            )
            ctx.trace.tool("execute", name=name, outcome="blocked_overwrite", risk=risk)
            self._emit_step(ctx, "execute", "blocked", action=name, reason="overwrite")
            ctx.transcript.append({
                "state": AgentState.EXECUTING.value, "action": name,
                "thoughts": thoughts,
                # Same shape as a staged proposal: the payload is never worth
                # replaying into the transcript, only the decision is.
                "args": {k: v for k, v in args.items() if k != "content"},
                "risk": risk,
                "governance": dict(policy),
                "permission_mode": mode.value,
                "change_class": overwrite.get("change_class"),
                "error": error,
            })
            d.audit(
                "agent_blocked", user_email=current_user,
                source=getattr(req, "source", None) or "agent",
                action=name, reason="overwrite_fail_closed",
                path=target or None,
                change_class=overwrite.get("change_class"),
                permission_mode=mode.value,
                governance=dict(policy),
            )
            return True

        reason = block_reason_for_tool(
            mode, name, policy, args,
            approved_by_human=bool(ctx.approved_by_human),
            governor_allows_additive=governor_allows_additive,
        )
        if reason is None:
            return False

        d.audit(
            "agent_exec", user_email=current_user, source=getattr(req, "source", None) or "agent",
            state=AgentState.EXECUTING.value, action=name, risk=risk,
            shell=policy["shell"], network=policy["network"],
            destructive=policy["destructive"], sandbox=policy["sandbox"],
            rollback=policy["rollback"],
            permission_mode=mode.value,
            args={k: v for k, v in args.items() if k != "content"},
        )
        ctx.trace.tool("execute", name=name, outcome="blocked_approval", risk=risk)
        self._emit_step(ctx, "execute", "blocked", action=name, reason="approval")
        ctx.transcript.append({
            "state": AgentState.EXECUTING.value, "action": name,
            "thoughts": thoughts, "args": args, "risk": risk,
            "governance": dict(policy),
            "permission_mode": mode.value,
            "error": reason,
        })
        return True

    def _dispatch_step(
        self, ctx: AgentRunContext, name: str, thoughts: str, args: dict,
        policy: Mapping[str, Any], risk: str, current_user: str,
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
