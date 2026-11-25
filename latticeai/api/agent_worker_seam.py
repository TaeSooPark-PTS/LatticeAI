from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.core.messages import http_error, resolve_language
from latticeai.core.permission_mode import is_circuit_breaker
from latticeai.core.tool_governor import classify_tool_call
from latticeai.tools import ToolError

SEAM_ENV_VAR = "LATTICEAI_AGENT_TOOL_SEAM"

MIN_MAX_TOKENS = 1
MAX_MAX_TOKENS = 8192
MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0

MAX_STOP_STRINGS = 8

MAX_PREFIX_CHARS = 256

SEAM_RATE_BUCKET = "agent_seam"


class AgentLLMRequest(BaseModel):
    model_id: Optional[str] = None
    message: str
    context: Optional[str] = None
    max_tokens: int = 4096
    temperature: float = 0.2
    stop: Optional[List[str]] = Field(default=None, max_length=MAX_STOP_STRINGS)
    prefix: Optional[str] = Field(default=None, max_length=MAX_PREFIX_CHARS)


class AgentToolRequest(BaseModel):
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    workspace_id: Optional[str] = None


def _seam_open() -> bool:
    return os.environ.get(SEAM_ENV_VAR) == "1"


def _path_probe(dispatch_service: Any, tool: str) -> Optional[Callable[[str], bool]]:
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
    require_user: Callable[[Request], Any],
    enforce_rate_limit: Callable[[str, str], None],
) -> APIRouter:
    router = APIRouter()

    def _require_seam(request: Request) -> None:
        if not _seam_open():
            raise http_error(404, "agent_seam.disabled", resolve_language(request))

    def _admit(request: Request) -> str:
        current_user = require_user(request)
        enforce_rate_limit(current_user, SEAM_RATE_BUCKET)
        return str(current_user or "")

    def _guard(tool: str, args: Dict[str, Any], language: str) -> Dict[str, Any]:
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
            stop=req.stop or None,
            prefix=req.prefix or None,
            cite_sources=False,
        )
        return {"text": str(text)}

    @router.post("/agent/tool")
    async def agent_tool(req: AgentToolRequest, request: Request):
        _require_seam(request)
        current_user = _admit(request)
        language = resolve_language(request)
        tool = req.tool.strip()
        if not tool:
            raise http_error(422, "agent_seam.tool_required", language)
        args = dict(req.args)

        _guard(tool, args, language)

        try:
            dispatch_service.check_role(tool, current_user)
        except PermissionError as exc:
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
            result = await asyncio.to_thread(_run)
        except (ToolError, KeyError, TypeError, PermissionError) as exc:
            return {"error": str(exc)}
        return {"result": result}

    return router


__all__ = [
    "MAX_MAX_TOKENS",
    "MAX_PREFIX_CHARS",
    "MAX_TEMPERATURE",
    "MIN_MAX_TOKENS",
    "MIN_TEMPERATURE",
    "SEAM_ENV_VAR",
    "SEAM_RATE_BUCKET",
    "AgentLLMRequest",
    "AgentToolRequest",
    "create_agent_worker_seam_router",
]
