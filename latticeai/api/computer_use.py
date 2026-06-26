"""Computer-use routes and the desktop-control agent loop."""

from __future__ import annotations

import json
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from latticeai.core.agent import extract_action as _extract_agent_action
from lattice_brain.runtime.hooks import dispatch_tool
from tools import (
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


def create_computer_use_router(*, model_router, require_user, tool_response, save_to_history, hooks=None) -> APIRouter:
    router = APIRouter()

    def _dispatch(name, args, fn):
        # Run a computer-use action through the unified pre_tool/post_tool
        # lifecycle. With hooks=None this is a transparent pass-through, so the
        # behaviour is unchanged when hooks are absent.
        return dispatch_tool(hooks, name, dict(args or {}), fn, source="computer_use")

    @router.get("/tools/chrome_status")
    async def tools_chrome_status(request: Request):
        require_user(request)
        return tool_response(desktop_bridge_status)

    @router.get("/tools/computer_use_status")
    async def tools_computer_use_status(request: Request):
        require_user(request)
        return tool_response(computer_status)

    @router.get("/cu/status")
    async def cu_status(request: Request):
        require_user(request)
        try:
            return _dispatch("computer_status", {}, computer_status)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/cu/screenshot")
    async def cu_screenshot(request: Request):
        require_user(request)
        try:
            return _dispatch("computer_screenshot", {}, computer_screenshot)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/cu/open_app")
    async def cu_open_app(req: CuOpenAppRequest, request: Request):
        require_user(request)
        return tool_response(computer_open_app, req.app)

    @router.post("/cu/open_url")
    async def cu_open_url(req: CuOpenUrlRequest, request: Request):
        require_user(request)
        return tool_response(computer_open_url, req.url, req.app)

    @router.post("/cu/click")
    async def cu_click(req: CuClickRequest, request: Request):
        require_user(request)
        return tool_response(computer_click, req.x, req.y, req.button, req.double)

    @router.post("/cu/type")
    async def cu_type(req: CuTypeRequest, request: Request):
        require_user(request)
        return tool_response(computer_type, req.text, req.interval)

    @router.post("/cu/key")
    async def cu_key(req: CuKeyRequest, request: Request):
        require_user(request)
        return tool_response(computer_key, req.key)

    @router.post("/cu/scroll")
    async def cu_scroll(req: CuScrollRequest, request: Request):
        require_user(request)
        return tool_response(computer_scroll, req.x, req.y, req.direction, req.clicks)

    @router.post("/cu/move")
    async def cu_move(req: CuMoveRequest, request: Request):
        require_user(request)
        return tool_response(computer_move, req.x, req.y)

    @router.post("/cu/drag")
    async def cu_drag(req: CuDragRequest, request: Request):
        require_user(request)
        return tool_response(computer_drag, req.x1, req.y1, req.x2, req.y2)

    @router.post("/cu/agent")
    async def cu_agent(req: CuAgentRequest, request: Request):
        require_user(request)

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
                        result = _dispatch("computer_open_url", {"url": url, "app": "Google Chrome"},
                                           lambda: computer_open_url(url, "Google Chrome"))
                        yield _send("result", {"step": 1, "action": "computer_open_url", "result": result})
                        message = f"Google Chrome에서 {url}을 열었습니다."
                        action_name = "computer_open_url"
                    else:
                        yield _send(
                            "action",
                            {"step": 1, "action": "computer_open_app", "args": {"app": "Google Chrome"}},
                        )
                        result = _dispatch("computer_open_app", {"app": "Google Chrome"},
                                           lambda: computer_open_app("Google Chrome"))
                        yield _send("result", {"step": 1, "action": "computer_open_app", "result": result})
                        message = "Google Chrome을 열었습니다."
                        action_name = "computer_open_app"
                    save_to_history("user", req.task, source="web", conversation_id=req.conversation_id)
                    save_to_history("assistant", message, source="web", conversation_id=req.conversation_id)
                    yield _send("final", {"message": message, "steps": [{"step": 1, "action": action_name, "result": result}]})
                except (ToolError, PermissionError) as exc:
                    yield _send("tool_error", {"step": 1, "action": "computer_open_app", "error": str(exc)})
                return

            if not model_router.current_model_id:
                yield _send("error", {"error": "No model loaded."})
                return

            transcript = []
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
                    save_to_history("user", req.task, source="web", conversation_id=req.conversation_id)
                    save_to_history("assistant", message, source="web", conversation_id=req.conversation_id)
                    yield _send("final", {"message": message, "steps": transcript})
                    return

                yield _send("action", {"step": step + 1, "action": name, "args": args})
                try:
                    result = _dispatch(name, args, lambda: execute_tool(name, args))
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
