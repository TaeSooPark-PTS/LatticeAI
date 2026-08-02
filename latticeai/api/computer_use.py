"""Computer-use routes and the desktop-control agent loop."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.core.agent import extract_action as _extract_agent_action
from latticeai.services.tool_dispatch import enforce_tool_policy
from latticeai.tools import (
    AGENT_ROOT,
    ToolError,
    computer_click,
    computer_drag,
    computer_key,
    computer_move,
    computer_open_app,
    computer_open_url,
    computer_screenshot,
    computer_scroll,
    computer_status,
    computer_type,
    desktop_bridge_status,
    execute_tool,
)

CU_SYSTEM_PROMPT = """You are Lattice AI desktop-control agent. You control the Mac desktop using tools.
Prefer non-visual direct actions when possible. Use screenshots only when you must inspect visible UI state or choose screen coordinates.

Available actions:
- computer_screenshot: {"action":"computer_screenshot","args":{}} — capture screen, returns screenshot_b64
- computer_open_app: {"action":"computer_open_app","args":{"app":"Google Chrome"}} — open or focus a Mac app
- computer_open_url: {"action":"computer_open_url","args":{"url":"https://example.com","app":"Google Chrome"}} — open URL in app
- computer_click: {"action":"computer_click","args":{"x":500,"y":300,"button":"left","double":false}}
- computer_type: {"action":"computer_type","args":{"text":"hello world","interval":0.04}}
- computer_key: {"action":"computer_key","args":{"key":"return"}} — keys: return, escape, tab, space, command+c, etc.
- computer_scroll: {"action":"computer_scroll","args":{"x":500,"y":300,"direction":"down","clicks":3}}
- computer_move: {"action":"computer_move","args":{"x":500,"y":300}}
- computer_drag: {"action":"computer_drag","args":{"x1":100,"y1":100,"x2":500,"y2":500}}
- vision_analyze: {"action":"vision_analyze","args":{"image_b64": "...", "prompt": "What do you see on screen?"}} — use after screenshot to let VLM describe/answer about the image (only when multimodal model loaded)
- final: {"action":"final","message":"Korean summary of what was accomplished"}

Rules:
- Respond with exactly ONE JSON object. No markdown, no extra text.
- Do not take screenshots for simple app launch, URL opening, keyboard shortcuts, or non-visual tasks.
- Take a screenshot before coordinate-based clicks/drags or when the task explicitly asks you to inspect the screen.
- After screenshot, prefer vision_analyze (with good prompt) over raw b64 in next step when you need to understand what is on screen (especially with multimodal/VLM loaded).
- After coordinate-based clicking or typing into an unknown focused field, take a screenshot only if verification is necessary.
- Use coordinates relative to the screen (0,0 is top-left).
- If a UI element is not visible, scroll or search for it first.
- macOS Accessibility permission required for mouse/keyboard control.
"""


class CuAgentRequest(BaseModel):
    task: str
    conversation_id: Optional[str] = None
    max_steps: int = 15
    temperature: float = 0.1


class CuClickRequest(BaseModel):
    x: int
    y: int
    button: str = "left"
    double: bool = False


class CuOpenAppRequest(BaseModel):
    app: str = "Google Chrome"


class CuOpenUrlRequest(BaseModel):
    url: str
    app: str = "Google Chrome"


class CuTypeRequest(BaseModel):
    text: str
    interval: float = 0.04


class CuKeyRequest(BaseModel):
    key: str


class CuScrollRequest(BaseModel):
    x: int
    y: int
    direction: str = "down"
    clicks: int = 3


class CuMoveRequest(BaseModel):
    x: int
    y: int


class CuDragRequest(BaseModel):
    x1: int
    y1: int
    x2: int
    y2: int


def create_computer_use_router(
    *,
    model_router,
    require_user,
    tool_response,
    save_to_history,
    hooks=None,
    append_audit_event=None,
    workspace_service=None,
) -> APIRouter:
    router = APIRouter()

    def _requested_workspace(request: Request) -> Optional[str]:
        header = request.headers.get("X-Workspace-Id")
        if header and header.strip():
            return header.strip()
        query = request.query_params.get("workspace_id")
        return query.strip() if query and query.strip() else None

    def _write_workspace(request: Request, current_user: str) -> Optional[str]:
        """Resolve only the CU agent's durable write scope.

        The other ``/cu`` actions remain host operations and do not acquire a
        workspace dependency.  A no-auth local request without an explicit
        scope preserves its legacy unscoped history behavior; explicit scopes
        and every authenticated request go through the normal write gate.
        """

        requested = _requested_workspace(request)
        if workspace_service is None:
            return requested
        if not current_user and requested is None:
            return None
        try:
            return workspace_service.resolve_write_scope(
                requested,
                current_user or None,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    def _audit_args(name: str, args: dict) -> dict:
        """Return audit-safe argument metadata without storing typed text."""
        args = dict(args or {})
        if name == "computer_type":
            return {"text_length": len(str(args.get("text") or "")), "interval": args.get("interval")}
        if name == "vision_analyze":
            return {
                "image_b64_length": len(str(args.get("image_b64") or "")),
                "prompt_length": len(str(args.get("prompt") or "")),
            }
        return args

    def _audit(name: str, args: dict, *, current_user: str, status: str, error: Optional[str] = None) -> None:
        if append_audit_event is None:
            return
        payload = {
            "user_email": current_user,
            "source": "computer_use",
            "tool": name,
            "status": status,
            "args": _audit_args(name, args),
        }
        if error:
            payload["error"] = error[:500]
        append_audit_event("computer_use_tool", **payload)

    def _dispatch(name, args, fn, *, current_user: str):
        # Run a computer-use action through the unified pre_tool/post_tool
        # lifecycle after the same ToolRegistry policy gate used by direct
        # /tools execution. This keeps desktop control behind one governance
        # boundary instead of relying on route-specific checks.
        args = dict(args or {})
        try:
            enforce_tool_policy(name, args, current_user=current_user, source="computer_use")
        except HTTPException as exc:
            _audit(name, args, current_user=current_user, status="blocked", error=str(exc.detail))
            raise
        try:
            result = dispatch_tool(hooks, name, args, fn, source="computer_use")
        except (PermissionError, ToolError, KeyError, TypeError) as exc:
            _audit(name, args, current_user=current_user, status="error", error=str(exc))
            raise
        _audit(name, args, current_user=current_user, status="ok")
        return result

    def _response(result):
        return {"status": "ok", "workspace": str(AGENT_ROOT), "result": result}

    @router.get("/tools/chrome_status")
    async def tools_chrome_status(request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "chrome_status",
            {},
            desktop_bridge_status,
            current_user=current_user,
        )
        return _response(result)

    @router.get("/tools/computer_use_status")
    async def tools_computer_use_status(request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "computer_use_status",
            {},
            computer_status,
            current_user=current_user,
        )
        return _response(result)

    @router.get("/cu/status")
    async def cu_status(request: Request):
        current_user = require_user(request)
        try:
            return _dispatch("computer_status", {}, computer_status, current_user=current_user)
        except HTTPException:
            raise
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/cu/screenshot")
    async def cu_screenshot(request: Request):
        current_user = require_user(request)
        try:
            return _dispatch("computer_screenshot", {}, computer_screenshot, current_user=current_user)
        except HTTPException:
            raise
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/cu/open_app")
    async def cu_open_app(req: CuOpenAppRequest, request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "computer_open_app",
            {"app": req.app},
            lambda: computer_open_app(req.app),
            current_user=current_user,
        )
        return _response(result)

    @router.post("/cu/open_url")
    async def cu_open_url(req: CuOpenUrlRequest, request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "computer_open_url",
            {"url": req.url, "app": req.app},
            lambda: computer_open_url(req.url, req.app),
            current_user=current_user,
        )
        return _response(result)

    @router.post("/cu/click")
    async def cu_click(req: CuClickRequest, request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "computer_click",
            {"x": req.x, "y": req.y, "button": req.button, "double": req.double},
            lambda: computer_click(req.x, req.y, req.button, req.double),
            current_user=current_user,
        )
        return _response(result)

    @router.post("/cu/type")
    async def cu_type(req: CuTypeRequest, request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "computer_type",
            {"text": req.text, "interval": req.interval},
            lambda: computer_type(req.text, req.interval),
            current_user=current_user,
        )
        return _response(result)

    @router.post("/cu/key")
    async def cu_key(req: CuKeyRequest, request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "computer_key",
            {"key": req.key},
            lambda: computer_key(req.key),
            current_user=current_user,
        )
        return _response(result)

    @router.post("/cu/scroll")
    async def cu_scroll(req: CuScrollRequest, request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "computer_scroll",
            {"x": req.x, "y": req.y, "direction": req.direction, "clicks": req.clicks},
            lambda: computer_scroll(req.x, req.y, req.direction, req.clicks),
            current_user=current_user,
        )
        return _response(result)

    @router.post("/cu/move")
    async def cu_move(req: CuMoveRequest, request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "computer_move",
            {"x": req.x, "y": req.y},
            lambda: computer_move(req.x, req.y),
            current_user=current_user,
        )
        return _response(result)

    @router.post("/cu/drag")
    async def cu_drag(req: CuDragRequest, request: Request):
        current_user = require_user(request)
        result = _dispatch(
            "computer_drag",
            {"x1": req.x1, "y1": req.y1, "x2": req.x2, "y2": req.y2},
            lambda: computer_drag(req.x1, req.y1, req.x2, req.y2),
            current_user=current_user,
        )
        return _response(result)

    @router.post("/cu/agent")
    async def cu_agent(req: CuAgentRequest, request: Request):
        current_user = require_user(request)
        workspace_id = _write_workspace(request, current_user)

        def _save_completed(role: str, message: str) -> None:
            save_to_history(
                role,
                message,
                source="web",
                conversation_id=req.conversation_id,
                user_email=current_user,
                workspace_id=workspace_id,
            )

        async def _stream():
            task_lower = (req.task or "").lower()
            url_match = re.search(r"(https?://[^\s]+|localhost:\d+[^\s]*|127\.0\.0\.1:\d+[^\s]*)", req.task or "")

            def _send(event: str, data: dict) -> str:
                return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            if ("chrome" in task_lower or "크롬" in task_lower) and any(
                word in task_lower for word in ["open", "열", "켜", "실행", "띄"]
            ):
                yield _send("start", {"task": req.task, "max_steps": 1})
                try:
                    if url_match:
                        url = url_match.group(1)
                        yield _send(
                            "action",
                            {"step": 1, "action": "computer_open_url", "args": {"url": url, "app": "Google Chrome"}},
                        )
                        result = _dispatch(
                            "computer_open_url",
                            {"url": url, "app": "Google Chrome"},
                            lambda: computer_open_url(url, "Google Chrome"),
                            current_user=current_user,
                        )
                        yield _send("result", {"step": 1, "action": "computer_open_url", "result": result})
                        message = f"Google Chrome에서 {url}을 열었습니다."
                        action_name = "computer_open_url"
                    else:
                        yield _send(
                            "action",
                            {"step": 1, "action": "computer_open_app", "args": {"app": "Google Chrome"}},
                        )
                        result = _dispatch(
                            "computer_open_app",
                            {"app": "Google Chrome"},
                            lambda: computer_open_app("Google Chrome"),
                            current_user=current_user,
                        )
                        yield _send("result", {"step": 1, "action": "computer_open_app", "result": result})
                        message = "Google Chrome을 열었습니다."
                        action_name = "computer_open_app"
                    _save_completed("user", req.task)
                    _save_completed("assistant", message)
                    yield _send("final", {"message": message, "steps": [{"step": 1, "action": action_name, "result": result}]})
                except HTTPException as exc:
                    yield _send("tool_error", {"step": 1, "action": "computer_open_app", "error": str(exc.detail)})
                except (ToolError, PermissionError) as exc:
                    yield _send("tool_error", {"step": 1, "action": "computer_open_app", "error": str(exc)})
                return

            if not model_router.current_model_id:
                yield _send("error", {"error": "No model loaded."})
                return

            transcript: List[Dict[str, Any]] = []
            last_screenshot_b64: Optional[str] = None
            max_steps = max(1, min(req.max_steps, 20))
            yield _send("start", {"task": req.task, "max_steps": max_steps})

            for step in range(max_steps):
                context = (
                    f"{CU_SYSTEM_PROMPT}\n\n"
                    f"Task: {req.task}\n\n"
                    f"Steps completed so far:\n{json.dumps(transcript, ensure_ascii=False, indent=2)}"
                )
                raw = await model_router.generate(
                    message="Choose the next computer use action.",
                    context=context,
                    image_data=last_screenshot_b64,
                    max_tokens=1024,
                    temperature=req.temperature,
                )

                try:
                    action = _extract_agent_action(str(raw))
                except ValueError as exc:
                    yield _send("error", {"step": step + 1, "error": str(exc), "raw": str(raw)})
                    break

                name = action.get("action")
                args = action.get("args") or {}
                if name == "final":
                    message = action.get("message", "작업을 완료했습니다.")
                    _save_completed("user", req.task)
                    _save_completed("assistant", message)
                    yield _send("final", {"message": message, "steps": transcript})
                    return

                yield _send("action", {"step": step + 1, "action": name, "args": args})
                try:
                    result = _dispatch(
                        name,
                        args,
                        # Bound as defaults; see chat_agent_http for why.
                        lambda name=name, args=args: execute_tool(name, args),
                        current_user=current_user,
                    )
                    if name == "computer_screenshot" and "screenshot_b64" in result:
                        last_screenshot_b64 = result["screenshot_b64"]
                        result_summary = {k: v for k, v in result.items() if k != "screenshot_b64"}
                        result_summary["screenshot_captured"] = True
                        transcript.append({"step": step + 1, "action": name, "args": args, "result": result_summary})
                        yield _send(
                            "screenshot",
                            {
                                "step": step + 1,
                                "screenshot_b64": last_screenshot_b64,
                                "width": result.get("screen_width"),
                                "height": result.get("screen_height"),
                            },
                        )
                    else:
                        last_screenshot_b64 = None
                        transcript.append({"step": step + 1, "action": name, "args": args, "result": result})
                        yield _send("result", {"step": step + 1, "action": name, "result": result})
                except HTTPException as exc:
                    error_str = str(exc.detail)
                    transcript.append({"step": step + 1, "action": name, "args": args, "error": error_str})
                    yield _send("tool_error", {"step": step + 1, "action": name, "error": error_str})
                except (ToolError, PermissionError, KeyError, TypeError) as exc:
                    error_str = str(exc)
                    transcript.append({"step": step + 1, "action": name, "args": args, "error": error_str})
                    yield _send("tool_error", {"step": step + 1, "action": name, "error": error_str})

            yield _send("done", {"steps": len(transcript), "transcript": transcript})

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router


__all__ = ["create_computer_use_router"]
