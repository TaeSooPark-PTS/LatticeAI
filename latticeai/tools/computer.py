"""Computer-use tools: screenshots and native desktop control."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import platform
import subprocess
import tempfile
import urllib.parse
import urllib.request
from os import environ as _os_environ
from typing import Any, Dict, Optional

from latticeai.core.quiet import quiet
from latticeai.tools import ToolError

_PLATFORM = platform.system()
_BRIDGE_TIMEOUT_SECONDS = 5


def _bridge_url() -> Optional[str]:
    """Read and validate LATTICEAI_COMPUTER_BRIDGE_URL (loopback only, fail-closed)."""
    raw = str(_os_environ.get("LATTICEAI_COMPUTER_BRIDGE_URL") or "").strip().rstrip("/")
    if not raw:
        return None
    parsed = urllib.parse.urlsplit(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https") or not parsed.netloc:
        raise ToolError(
            "LATTICEAI_COMPUTER_BRIDGE_URL must use http:// or https:// on loopback."
        )
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ToolError("LATTICEAI_COMPUTER_BRIDGE_URL contains an invalid authority.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ToolError(f"LATTICEAI_COMPUTER_BRIDGE_URL has an invalid port: {exc}") from exc
    if port is not None and (port <= 0 or port > 65535):
        raise ToolError("LATTICEAI_COMPUTER_BRIDGE_URL has an invalid port.")

    host = (parsed.hostname or "").strip().rstrip(".").lower()
    is_loopback = host == "localhost"
    if not is_loopback and host:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ToolError(
            f"LATTICEAI_COMPUTER_BRIDGE_URL must target a loopback host (127.0.0.1, localhost, or [::1]), got '{host}'."
        )
    return raw


def _bridge_get_json(url: str, timeout: int = _BRIDGE_TIMEOUT_SECONDS) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        raise ToolError(f"computer bridge GET failed ({url}): {exc}") from exc


def _bridge_post_act(base: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{base}/ui/act"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_BRIDGE_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8", errors="replace") or "{}"
            data = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        raise ToolError(f"computer bridge /ui/act failed: {exc}") from exc

    merged = dict(payload)
    if isinstance(data, dict):
        merged.update(data)
    merged["mode"] = "bridge"
    return merged


def computer_ui_inspect(app: Optional[str] = None) -> Dict[str, Any]:
    """Inspect active desktop UI and accessibility element tree."""
    clean_app = str(app or "").strip()
    base = _bridge_url()
    if base is not None:
        url = (
            f"{base}/ui/inspect?app={urllib.parse.quote(clean_app)}"
            if clean_app
            else f"{base}/ui/inspect"
        )
        data = _bridge_get_json(url)
        elements = data.get("elements")
        return {
            **data,
            "available": True,
            "mode": "bridge",
            "frontmost_app": data.get("frontmost_app", clean_app),
            "window_title": data.get("window_title", ""),
            "screen_size": data.get("screen_size", {"width": 0, "height": 0}),
            "elements": elements if isinstance(elements, list) else [],
        }

    if _PLATFORM == "Darwin":
        frontmost = clean_app
        try:
            proc = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true',
                ],
                capture_output=True,
                text=True,
                timeout=_BRIDGE_TIMEOUT_SECONDS,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                frontmost = proc.stdout.strip()
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, RuntimeError):
            quiet()
        return {
            "available": False,
            "mode": "osascript_limited",
            "frontmost_app": frontmost,
            "window_title": "",
            "screen_size": {"width": 0, "height": 0},
            "elements": [],
            "note": "Accessibility element tree requires LATTICEAI_COMPUTER_BRIDGE_URL or macOS Accessibility permissions.",
        }

    return {
        "available": False,
        "mode": "unsupported",
        "frontmost_app": clean_app,
        "window_title": "",
        "screen_size": {"width": 0, "height": 0},
        "elements": [],
        "note": "UI accessibility inspection is not supported on this platform without LATTICEAI_COMPUTER_BRIDGE_URL.",
    }


# ── Computer Use ──────────────────────────────────────────────────────────────
_CU_AVAILABLE = False
# Optional dependency: None until _init_computer_use() imports it, and the
# _CU_AVAILABLE flag is what every call site checks.
_pyautogui: Any = None

def _init_computer_use():
    global _CU_AVAILABLE, _pyautogui
    try:
        import pyautogui as _pag
        _pag.FAILSAFE = True   # 마우스를 좌상단 코너로 이동하면 중단
        _pag.PAUSE = 0.25
        _pyautogui = _pag
        _CU_AVAILABLE = True
    except (ImportError, OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):
        quiet()

_init_computer_use()


def computer_screenshot() -> Dict[str, Any]:
    """현재 화면 전체를 캡처하여 base64 PNG로 반환합니다."""
    base = _bridge_url()
    if base is not None:
        data = _bridge_get_json(f"{base}/ui/screenshot", timeout=10)
        b64 = str(data.get("screenshot_b64") or "")
        return {
            **data,
            "screenshot_b64": b64,
            "format": str(data.get("format") or "png"),
            "bytes": int(data.get("bytes") or len(b64)),
            "screen_width": int(data.get("screen_width") or 0),
            "screen_height": int(data.get("screen_height") or 0),
            "mode": "bridge",
        }
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
        tmp = tmp_file.name
    try:
        if _PLATFORM == "Darwin":
            r = subprocess.run(
                ["screencapture", "-x", "-t", "png", tmp],
                capture_output=True, timeout=10, check=False,
            )
            if r.returncode != 0:
                raise ToolError(f"screencapture 실패: {r.stderr.decode()}")
        elif _CU_AVAILABLE:
            # Windows / Linux: use pyautogui screenshot
            screenshot = _pyautogui.screenshot()
            screenshot.save(tmp)
        else:
            raise ToolError("스크린샷 불가: macOS 전용 screencapture 또는 pyautogui 필요")
        with open(tmp, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        size = os.path.getsize(tmp)
        w, h = (_pyautogui.size() if _CU_AVAILABLE else (0, 0))
        return {
            "screenshot_b64": b64,
            "format": "png",
            "bytes": size,
            "screen_width": int(w),
            "screen_height": int(h),
        }
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            quiet()


def computer_click(x: int, y: int, button: str = "left", double: bool = False) -> Dict[str, Any]:
    """화면 좌표 (x, y)를 클릭합니다."""
    base = _bridge_url()
    if base is not None:
        x_int, y_int = int(x), int(y)
        return _bridge_post_act(
            base,
            {
                "action": "click",
                "x": x_int,
                "y": y_int,
                "button": str(button),
                "double": bool(double),
            },
        )
    if not _CU_AVAILABLE:
        raise ToolError("pyautogui를 사용할 수 없습니다.")
    x, y = int(x), int(y)
    if double:
        _pyautogui.doubleClick(x, y)
    elif button == "right":
        _pyautogui.rightClick(x, y)
    elif button == "middle":
        _pyautogui.middleClick(x, y)
    else:
        _pyautogui.click(x, y)
    return {"action": "click", "x": x, "y": y, "button": button, "double": double}


def computer_type(text: str, interval: float = 0.04) -> Dict[str, Any]:
    """현재 포커스된 위치에 텍스트를 입력합니다."""
    base = _bridge_url()
    if base is not None:
        raw_text = str(text)
        echo_text = (raw_text[:60] + "...") if len(raw_text) > 60 else raw_text
        resp = _bridge_post_act(
            base,
            {"action": "type", "text": raw_text, "interval": float(interval)},
        )
        return {
            **resp,
            "action": "type",
            "text": echo_text,
            "chars": len(raw_text),
            "mode": "bridge",
        }
    if not _CU_AVAILABLE:
        raise ToolError("pyautogui를 사용할 수 없습니다.")
    _pyautogui.write(str(text), interval=float(interval))
    return {"action": "type", "text": (text[:60] + "...") if len(text) > 60 else text, "chars": len(text)}


def computer_key(key: str) -> Dict[str, Any]:
    """키보드 키를 누릅니다. 예: 'return', 'escape', 'command+c', 'tab'"""
    base = _bridge_url()
    if base is not None:
        return _bridge_post_act(base, {"action": "key", "key": str(key)})
    if not _CU_AVAILABLE:
        raise ToolError("pyautogui를 사용할 수 없습니다.")
    key = str(key)
    if "+" in key:
        _pyautogui.hotkey(*key.split("+"))
    else:
        _pyautogui.press(key)
    return {"action": "key", "key": key}


def computer_scroll(x: int, y: int, direction: str = "down", clicks: int = 3) -> Dict[str, Any]:
    """화면 좌표에서 스크롤합니다."""
    base = _bridge_url()
    if base is not None:
        return _bridge_post_act(
            base,
            {
                "action": "scroll",
                "x": int(x),
                "y": int(y),
                "direction": str(direction),
                "clicks": int(clicks),
            },
        )
    if not _CU_AVAILABLE:
        raise ToolError("pyautogui를 사용할 수 없습니다.")
    x, y, clicks = int(x), int(y), int(clicks)
    _pyautogui.moveTo(x, y)
    amount = -clicks if direction == "down" else clicks
    _pyautogui.scroll(amount)
    return {"action": "scroll", "x": x, "y": y, "direction": direction, "clicks": clicks}


def computer_move(x: int, y: int) -> Dict[str, Any]:
    """마우스를 좌표로 이동합니다 (클릭 없음)."""
    base = _bridge_url()
    if base is not None:
        return _bridge_post_act(base, {"action": "move", "x": int(x), "y": int(y)})
    if not _CU_AVAILABLE:
        raise ToolError("pyautogui를 사용할 수 없습니다.")
    _pyautogui.moveTo(int(x), int(y), duration=0.2)
    return {"action": "move", "x": int(x), "y": int(y)}


def computer_drag(x1: int, y1: int, x2: int, y2: int) -> Dict[str, Any]:
    """(x1,y1)에서 (x2,y2)로 드래그합니다."""
    base = _bridge_url()
    if base is not None:
        return _bridge_post_act(
            base,
            {
                "action": "drag",
                "from": [int(x1), int(y1)],
                "to": [int(x2), int(y2)],
            },
        )
    if not _CU_AVAILABLE:
        raise ToolError("pyautogui를 사용할 수 없습니다.")
    _pyautogui.moveTo(int(x1), int(y1))
    _pyautogui.dragTo(int(x2), int(y2), duration=0.35, button="left")
    return {"action": "drag", "from": [int(x1), int(y1)], "to": [int(x2), int(y2)]}


def computer_status() -> Dict[str, Any]:
    """Computer Use 기능 사용 가능 여부를 확인합니다."""
    if not _CU_AVAILABLE:
        return {"available": False, "reason": "pyautogui not installed"}
    w, h = _pyautogui.size()
    return {
        "available": True,
        "screen_size": {"width": int(w), "height": int(h)},
        "failsafe": _pyautogui.FAILSAFE,
        "note": "macOS Accessibility 권한이 필요합니다 (시스템 설정 > 개인 정보 보호 > 손쉬운 사용)",
    }


def vision_analyze(image_b64: str, prompt: str = "Describe this image in detail. What do you see? Be concise and factual.") -> Dict[str, Any]:
    """Analyze an image using the loaded multimodal VLM (if current model supports vision).

    Designed to pair perfectly with computer_screenshot() output (screenshot_b64).
    Returns structured data that the agent runtime / chat can feed directly to
    model_router.generate(..., image_data=image_b64) when a VLM is active.
    Fits existing computer-use loop, KG ingestion, and agent tools without breaking
    non-VLM paths (graceful fallback in calling code).
    """
    if not image_b64 or not isinstance(image_b64, str) or len(image_b64) < 20:
        raise ToolError("Valid image_b64 (base64 image) required")
    prompt = (prompt or "Describe this image.").strip()[:2000]
    return {
        "action": "vision_analyze",
        "image_b64": image_b64[:80] + "..." if len(image_b64) > 80 else image_b64,
        "prompt": prompt,
        "supports_vlm": True,
        "note": "When multimodal model loaded, pass image_b64 as image_data + prompt to generate().",
    }
