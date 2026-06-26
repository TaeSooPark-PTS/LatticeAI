"""Computer-use tools: screenshots and native desktop control."""

from __future__ import annotations

import base64
import os
import platform
import subprocess
import tempfile
from typing import Any, Dict

from tools import ToolError

_PLATFORM = platform.system()


# ── Computer Use ──────────────────────────────────────────────────────────────
_CU_AVAILABLE = False
_pyautogui = None

def _init_computer_use():
    global _CU_AVAILABLE, _pyautogui
    try:
        import pyautogui as _pag
        _pag.FAILSAFE = True   # 마우스를 좌상단 코너로 이동하면 중단
        _pag.PAUSE = 0.25
        _pyautogui = _pag
        _CU_AVAILABLE = True
    except Exception:
        pass

_init_computer_use()


def computer_screenshot() -> Dict[str, Any]:
    """현재 화면 전체를 캡처하여 base64 PNG로 반환합니다."""
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
            pass


def computer_open_app(app: str = "Google Chrome") -> Dict[str, Any]:
    """앱을 실행하거나 앞으로 가져옵니다 (macOS/Windows/Linux)."""
    app = str(app or "Google Chrome").strip()
    if not app:
        raise ToolError("앱 이름이 필요합니다.")
    if _PLATFORM == "Darwin":
        cmd = ["open", "-a", app]
    elif _PLATFORM == "Windows":
        cmd = ["cmd", "/c", "start", "", app]
    else:
        cmd = ["xdg-open", app]
    r = subprocess.run(cmd, capture_output=True, timeout=10, check=False)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(f"앱 열기 실패: {err or app}")
    return {"action": "open_app", "app": app}


def computer_open_url(url: str, app: str = "Google Chrome") -> Dict[str, Any]:
    """URL을 브라우저로 엽니다 (macOS/Windows/Linux)."""
    url = str(url or "").strip()
    app = str(app or "").strip()
    if not url:
        raise ToolError("URL이 필요합니다.")
    if "://" not in url and not url.startswith(("localhost", "127.0.0.1")):
        url = "https://" + url
    if _PLATFORM == "Darwin":
        cmd = ["open", "-a", app, url] if app else ["open", url]
    elif _PLATFORM == "Windows":
        cmd = ["cmd", "/c", "start", "", url]
    else:
        cmd = ["xdg-open", url]
    r = subprocess.run(cmd, capture_output=True, timeout=10, check=False)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", errors="replace").strip()
        raise ToolError(f"URL 열기 실패: {err or url}")
    return {"action": "open_url", "app": app or "default", "url": url}


def computer_click(x: int, y: int, button: str = "left", double: bool = False) -> Dict[str, Any]:
    """화면 좌표 (x, y)를 클릭합니다."""
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
    if not _CU_AVAILABLE:
        raise ToolError("pyautogui를 사용할 수 없습니다.")
    _pyautogui.write(str(text), interval=float(interval))
    return {"action": "type", "text": (text[:60] + "...") if len(text) > 60 else text, "chars": len(text)}


def computer_key(key: str) -> Dict[str, Any]:
    """키보드 키를 누릅니다. 예: 'return', 'escape', 'command+c', 'tab'"""
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
    if not _CU_AVAILABLE:
        raise ToolError("pyautogui를 사용할 수 없습니다.")
    x, y, clicks = int(x), int(y), int(clicks)
    _pyautogui.moveTo(x, y)
    amount = -clicks if direction == "down" else clicks
    _pyautogui.scroll(amount)
    return {"action": "scroll", "x": x, "y": y, "direction": direction, "clicks": clicks}


def computer_move(x: int, y: int) -> Dict[str, Any]:
    """마우스를 좌표로 이동합니다 (클릭 없음)."""
    if not _CU_AVAILABLE:
        raise ToolError("pyautogui를 사용할 수 없습니다.")
    _pyautogui.moveTo(int(x), int(y), duration=0.2)
    return {"action": "move", "x": int(x), "y": int(y)}


def computer_drag(x1: int, y1: int, x2: int, y2: int) -> Dict[str, Any]:
    """(x1,y1)에서 (x2,y2)로 드래그합니다."""
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
