"""AI-Worker seam (v11.5.1, plan §Y1) — the three calls the Rust loop makes back.

Once the agent loop moves into ``lattice-agent`` (plan §Y2), Python stops being
the orchestrator and becomes exactly what the system diagram already draws: the
**AI Worker**. It infers with the loaded model, it runs tool handlers, and it
stages proposals. The Rust kernel decides *what* to do next; it has to call
Python to actually do it.

Reconnaissance found no surface it could call:

* there is no bare completion endpoint — every LLM route (``/chat``,
  ``/agent``) also writes history, assembles context, or drives the whole
  Python loop, none of which a Rust orchestrator wants;
* HTTP cannot create a change proposal at all — ``ChangeProposalService.review``
  is reachable only from the in-process agent runtime;
* ``/tools/*`` is the **direct** surface: it runs ``enforce_policy``, which
  denies anything not auto-approved (403) and never stages a proposal. Correct
  for a human clicking a button, useless for a governed loop.

So this module adds the three seams, and nothing else:

``POST /agent/llm``
    One completion. No history, no context assembly, no persistence of any
    kind — the whole body is one ``generate_as`` await.

``POST /agent/tool``
    One governed tool call: the mode-invariant guards, the role check, then the
    shared ``pre_tool`` → execute → ``post_tool`` lifecycle.

``POST /agent/change-proposal``
    The governor's verdict, verbatim, so the Rust loop can take the
    proposal-first path for edits to existing files.

Two boundaries stated here so the payloads are not read as more than they are:

* **The guards are re-run on the server, always.** The Rust kernel preflights
  permission mode before it ever calls; this seam still re-derives the policy,
  still asks :func:`~latticeai.core.permission_mode.is_circuit_breaker`, and
  still asks :func:`~latticeai.core.tool_governor.classify_tool_call`. A
  compromised or buggy kernel therefore cannot widen what Python will execute —
  the mode-invariant denials are defence in depth, not a duplicated preflight.
  What the seam deliberately does *not* own is mode gating itself (which of the
  approval-requiring steps may run): that is the kernel's decision, made with
  the run's approval state, which HTTP does not have.

* **``workspace_id`` is attribution, not authorization.** Tools resolve their
  own paths under ``AGENT_ROOT`` (or the home sandbox, via their own guards);
  they are not workspace-scoped resources the way ``/api/*`` reads are. The
  field is forwarded to the hook lifecycle so an audit event lands in the right
  workspace, and it is not used to widen or narrow what may run. A caller
  cannot reach another workspace's data by naming it here, because no code path
  downstream consults it for that.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.core.messages import http_error, resolve_language
from latticeai.core.permission_mode import is_circuit_breaker
from latticeai.core.tool_governor import classify_tool_call
from latticeai.tools import ToolError

#: Host-injected switch. Off by default: the loop seam is for a worker the
#: ``lattice-host`` supervisor started for itself, never for a browser that
#: happens to hold a session cookie. Read per request, not at import, so the
#: answer follows the process environment as it actually is.
SEAM_ENV_VAR = "LATTICEAI_AGENT_TOOL_SEAM"

#: Bounds on one completion. The ceiling is twice ``generate_as``'s own default
#: — enough for a long verification pass, short of a request that would hold the
#: single MLX executor for minutes.
MIN_MAX_TOKENS = 1
MAX_MAX_TOKENS = 8192
MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0

#: Rate-limit bucket. Deliberately *not* the ``"agent"`` bucket ``/agent`` uses:
#: that one is sized per *run* (10 burst, one refill per 10s) because one HTTP
#: call there is a whole agent run. Here one call is a single loop step, and a
#: Rust run makes a dozen of them, so reusing ``"agent"`` would 429 the loop
#: mid-run. This key is absent from ``_RATE_LIMITS``, so it takes the module
#: default (60 burst, 1/s) — a real per-user ceiling at per-step granularity.
SEAM_RATE_BUCKET = "agent_seam"


class AgentLLMRequest(BaseModel):
    """One completion, with the model chosen per call.

    ``model_id`` omitted means the router's current default; a model that is
    not cached makes ``generate_as`` answer ``"No model."``, which is returned
    verbatim rather than dressed up as an error — the loop records it as the
    step's text and re-plans, exactly as the Python loop does.
    """

    model_id: Optional[str] = None
    message: str
    context: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.2


class AgentToolRequest(BaseModel):
    """One governed tool call on behalf of the authenticated user."""

    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    workspace_id: Optional[str] = None


class AgentChangeProposalRequest(BaseModel):
    """A governor consultation for a write that may touch existing content.

    ``policy`` is optional: the Rust kernel already holds the policy it
    preflighted with, and passing it back keeps the two sides deciding on the
    same facts. Omitted, the registry's own policy for this call is used.
    """

    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    policy: Optional[Dict[str, Any]] = None
    workspace_id: Optional[str] = None
    conversation_id: Optional[str] = None


def _seam_open() -> bool:
    """Whether this process is a worker the host opened the seam for."""
    return os.environ.get(SEAM_ENV_VAR) == "1"


def _path_probe(
    dispatch_service: Any, tool: str
) -> Optional[Callable[[str], bool]]:
    """The *same* existence probe the direct surface classifies with.

    ``classify_tool_call`` asks "does the target already exist?" to tell an
    additive create from an overwrite. ``ToolDispatchService`` answers that
    through ``_governed_path_exists``, which resolves document creators'
    ``filename`` through their real output directory first — checking the raw
    argument inspects a path nothing ever writes. Reusing that method is the
    point: a second, subtly different probe here would let this seam and
    ``/tools/*`` disagree about whether a file exists, and the disagreement
    would show up as one surface staging a proposal while the other overwrites.

    ``classify_tool_call`` types ``path_exists`` as optional, so a dispatch
    service without the method (an injected fake, a future slimmer port) yields
    ``None`` and every target-write call classifies as additive. That is the
    weaker guard, and it is the honest one: claiming a file does not exist is
    what "we could not look" means to this classifier.
    """
    probe = getattr(dispatch_service, "_governed_path_exists", None)
    if not callable(probe):
        return None
    return lambda candidate: bool(probe(tool, candidate))


def create_agent_worker_seam_router(
    *,
    model_router: Any,
    dispatch_service: Any,
    execute_tool: Callable[[str, Dict[str, Any]], Any],
    hooks: Any,
    change_proposals: Any,
    require_user: Callable[[Request], Any],
    enforce_rate_limit: Callable[[str, str], None],
) -> APIRouter:
    router = APIRouter()

    def _require_seam(request: Request) -> None:
        """404 unless the host opened the seam for this worker.

        The detail says *why* rather than imitating a missing route. Hiding it
        would buy nothing: the paths are in the generated OpenAPI schema either
        way, this is a local-first Brain rather than a multi-tenant service, and
        an operator who forgot the environment variable otherwise gets a bare
        404 with nothing to act on.
        """
        if not _seam_open():
            raise http_error(404, "agent_seam.disabled", resolve_language(request))

    def _admit(request: Request) -> str:
        """Authenticate and charge this call against the per-step budget."""
        current_user = require_user(request)
        enforce_rate_limit(current_user, SEAM_RATE_BUCKET)
        return str(current_user or "")

    def _guard(tool: str, args: Dict[str, Any], language: str) -> Dict[str, Any]:
        """The mode-invariant denials, re-derived server-side.

        One call to :func:`is_circuit_breaker` covers both denials the plan
        names. The destructive-policy check is *inside* it — ``permission_mode``
        answers ``"destructive action is always blocked"`` for
        ``policy["destructive"]`` or ``risk == "destructive"`` before it looks
        at anything else. Writing a second, separate destructive check here (as
        ``enforce_policy`` does) would be code that can never run, and
        unreachable code is not a guard.
        """
        policy = dispatch_service.policy_for(tool, args)
        breaker = is_circuit_breaker(tool, dict(policy), args)
        if breaker:
            raise http_error(
                403,
                "agent_seam.tool_blocked",
                language,
                tool=tool,
                reason=breaker,
            )
        verdict = classify_tool_call(
            tool,
            args,
            policy=dict(policy),
            path_exists=_path_probe(dispatch_service, tool),
        )
        if verdict.get("fail_closed"):
            raise http_error(
                409,
                "agent_seam.tool_fail_closed",
                language,
                tool=tool,
                reason=str(verdict.get("reason") or ""),
            )
        return dict(policy)

    @router.post("/agent/llm")
    async def agent_llm(req: AgentLLMRequest, request: Request):
        """Generate once. Writes nothing, remembers nothing.

        Not gated by ``LATTICEAI_AGENT_TOOL_SEAM``: a completion has no side
        effect to gate, and the Rust loop is not the only caller that wants one
        (``/rust/context/document`` composes a prompt the same way). Auth and
        the per-step rate limit are the whole ceremony.

        Structural problems in the body — a missing ``message``, a string where
        a number belongs — answer with FastAPI's own 422, uniform with every
        other router. Semantic ones answer in the caller's language.
        """
        _admit(request)
        language = resolve_language(request)
        if not req.message.strip():
            raise http_error(422, "agent_seam.message_required", language)
        if req.max_tokens < MIN_MAX_TOKENS or req.max_tokens > MAX_MAX_TOKENS:
            raise http_error(
                422,
                "agent_seam.max_tokens_out_of_range",
                language,
                min=MIN_MAX_TOKENS,
                max=MAX_MAX_TOKENS,
            )
        if req.temperature < MIN_TEMPERATURE or req.temperature > MAX_TEMPERATURE:
            raise http_error(
                422,
                "agent_seam.temperature_out_of_range",
                language,
                min=MIN_TEMPERATURE,
                max=MAX_TEMPERATURE,
            )
        text = await model_router.generate_as(
            req.model_id or None,
            message=req.message,
            context=req.context,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )
        return {"text": str(text)}

    @router.post("/agent/tool")
    async def agent_tool(req: AgentToolRequest, request: Request):
        """Run one tool through the same lifecycle the Python loop uses.

        The answer shape mirrors the loop's own catch (``execution.py``): a
        ``ToolError``/``KeyError``/``TypeError``/``PermissionError`` is the
        *step's* outcome, not the request's, so it comes back 200 with
        ``{"error": ...}`` for the transcript. A denial — role, circuit
        breaker, fail-closed governance — is the request's outcome and comes
        back 4xx, because retrying it would be pointless.
        """
        _require_seam(request)
        current_user = _admit(request)
        language = resolve_language(request)
        tool = req.tool.strip()
        if not tool:
            raise http_error(422, "agent_seam.tool_required", language)
        args = dict(req.args)

        _guard(tool, args, language)

        # After the mode-invariant denials, deliberately: they are the same
        # answer for every role, so they need no user-table read to decide.
        try:
            dispatch_service.check_role(tool, current_user)
        except PermissionError as exc:
            # The shipped service raises HTTPException(403) here and that
            # propagates untouched; a port that speaks Python's own
            # authorization error gets the same status rather than a 500.
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        def _run() -> Any:
            return dispatch_tool(
                hooks,
                tool,
                args,
                lambda: execute_tool(tool, args),
                user_email=current_user,
                workspace_id=req.workspace_id,
                source="agent",
            )

        try:
            # Off the loop: tool handlers open files, shell out, and write the
            # graph, and this server has one event loop for every user (10.9.0).
            result = await asyncio.to_thread(_run)
        except (ToolError, KeyError, TypeError, PermissionError) as exc:
            return {"error": str(exc)}
        return {"result": result}

    @router.post("/agent/change-proposal")
    async def agent_change_proposal(
        req: AgentChangeProposalRequest, request: Request
    ):
        """Ask the governor what should happen to this write.

        The verdict is returned verbatim — ``{"decision": "allow_additive"}``
        or ``{"decision": "proposed", "proposal": {...}}`` — because the Rust
        loop has to act on the same facts the Review Center will show.

        ``review`` answers ``None`` for "I have nothing to say about this call:
        fall through to the normal gates". Three different situations collapse
        into that one ``None`` (not a governed tool; no proposal required; the
        edit could not be computed deterministically) and the service does not
        distinguish them, so neither does this payload: ``{"decision": "none"}``
        and nothing invented about why.

        No mode-invariant guard here, because nothing executes: staging a
        proposal writes a review item, and the write itself still has to come
        back through ``/agent/tool`` — where the guards are.
        """
        _require_seam(request)
        current_user = _admit(request)
        language = resolve_language(request)
        if change_proposals is None:
            raise http_error(503, "agent_seam.proposals_unavailable", language)
        tool = req.tool.strip()
        if not tool:
            raise http_error(422, "agent_seam.tool_required", language)
        args = dict(req.args)
        policy = (
            dict(req.policy)
            if req.policy is not None
            else dict(dispatch_service.policy_for(tool, args))
        )

        def _review() -> Optional[Dict[str, Any]]:
            return change_proposals.review(
                tool,
                args,
                policy=policy,
                user_email=current_user,
                workspace_id=req.workspace_id,
                conversation_id=req.conversation_id,
            )

        # Staging reads the target, computes a diff, and writes a review item —
        # all blocking I/O, none of it belonging on the event loop.
        verdict = await asyncio.to_thread(_review)
        if verdict is None:
            return {"decision": "none"}
        return verdict

    return router


__all__ = [
    "MAX_MAX_TOKENS",
    "MAX_TEMPERATURE",
    "MIN_MAX_TOKENS",
    "MIN_TEMPERATURE",
    "SEAM_ENV_VAR",
    "SEAM_RATE_BUCKET",
    "AgentChangeProposalRequest",
    "AgentLLMRequest",
    "AgentToolRequest",
    "create_agent_worker_seam_router",
]
