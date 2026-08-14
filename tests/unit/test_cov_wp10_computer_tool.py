"""Coverage for the desktop-control tool module (latticeai/tools/computer.py).

Nothing here may touch a real screen, mouse, keyboard, or process: every
platform seam is replaced.  ``pyautogui`` is injected as a fake module through
``sys.modules`` so the "optional dependency present" branches execute on a
headless CI box that will never have it installed, ``subprocess`` is swapped
for a recorder, and the screenshot temp file is redirected into ``tmp_path``.
"""

from __future__ import annotations

import base64
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from latticeai.tools import ToolError, computer

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-capture"


# ── fakes ────────────────────────────────────────────────────────────────────
class _Completed:
    def __init__(self, returncode: int = 0, stderr: bytes = b""):
        self.returncode = returncode
        self.stderr = stderr


def _fake_subprocess(calls, *, returncode=0, stderr=b"", writes_png_to_last_arg=False):
    def run(cmd, **kwargs):
        cmd = list(cmd)
        calls.append((cmd, kwargs))
        if writes_png_to_last_arg:
            Path(cmd[-1]).write_bytes(PNG_BYTES)
        return _Completed(returncode, stderr)

    return SimpleNamespace(run=run)


def _fake_pyautogui(calls, *, size=(1920, 1080)):
    module = types.ModuleType("pyautogui")

    def _recorder(name):
        def _call(*args, **kwargs):
            calls.append((name, args, kwargs))

        return _call

    for name in (
        "click", "doubleClick", "rightClick", "middleClick",
        "write", "press", "hotkey", "moveTo", "dragTo", "scroll",
    ):
        setattr(module, name, _recorder(name))

    class _Screenshot:
        def save(self, path):
            calls.append(("save", (path,), {}))
            Path(path).write_bytes(PNG_BYTES)

    def screenshot():
        calls.append(("screenshot", (), {}))
        return _Screenshot()

    module.screenshot = screenshot
    module.size = lambda: size
    return module


def _enable_pyautogui(monkeypatch, calls, *, size=(1920, 1080)):
    """Import a fake pyautogui through the module's real init path."""
    module = _fake_pyautogui(calls, size=size)
    # Recorded before _init_computer_use() mutates them, so monkeypatch's
    # teardown restores the process-wide "no pyautogui installed" state.
    monkeypatch.setattr(computer, "_CU_AVAILABLE", False)
    monkeypatch.setattr(computer, "_pyautogui", None)
    monkeypatch.setitem(sys.modules, "pyautogui", module)
    computer._init_computer_use()
    return module


def _disable_pyautogui(monkeypatch):
    monkeypatch.setattr(computer, "_CU_AVAILABLE", False)
    monkeypatch.setattr(computer, "_pyautogui", None)


@pytest.fixture()
def screenshot_tmpdir(monkeypatch, tmp_path):
    """Keep computer_screenshot()'s NamedTemporaryFile inside tmp_path."""
    monkeypatch.setattr(computer.tempfile, "tempdir", str(tmp_path))
    return tmp_path


# ── optional-dependency init ─────────────────────────────────────────────────
def test_init_computer_use_arms_failsafe_when_pyautogui_imports():
    calls = []
    with pytest.MonkeyPatch.context() as monkeypatch:
        module = _enable_pyautogui(monkeypatch, calls)

        assert computer._CU_AVAILABLE is True
        assert computer._pyautogui is module
        assert module.FAILSAFE is True
        assert module.PAUSE == 0.25

    # Undone: the headless process is back to "pyautogui unavailable".
    assert computer._CU_AVAILABLE is False
    assert computer._pyautogui is None


def test_init_computer_use_stays_unavailable_when_the_backend_is_broken(monkeypatch):
    """A pyautogui that cannot arm itself (no DISPLAY) leaves CU switched off."""

    class _BrokenBackend:
        def __setattr__(self, name, value):
            raise RuntimeError("no DISPLAY available")

    monkeypatch.setattr(computer, "_CU_AVAILABLE", False)
    monkeypatch.setattr(computer, "_pyautogui", None)
    monkeypatch.setitem(sys.modules, "pyautogui", _BrokenBackend())

    computer._init_computer_use()

    assert computer._CU_AVAILABLE is False
    assert computer._pyautogui is None


# ── computer_screenshot ──────────────────────────────────────────────────────
def test_screenshot_uses_screencapture_on_darwin(monkeypatch, screenshot_tmpdir):
    calls = []
    _disable_pyautogui(monkeypatch)
    monkeypatch.setattr(computer, "_PLATFORM", "Darwin")
    monkeypatch.setattr(
        computer, "subprocess",
        _fake_subprocess(calls, writes_png_to_last_arg=True),
    )

    result = computer.computer_screenshot()

    cmd, kwargs = calls[0]
    assert cmd[:4] == ["screencapture", "-x", "-t", "png"]
    assert kwargs["timeout"] == 10
    assert base64.b64decode(result["screenshot_b64"]) == PNG_BYTES
    assert result["format"] == "png"
    assert result["bytes"] == len(PNG_BYTES)
    assert (result["screen_width"], result["screen_height"]) == (0, 0)
    assert not Path(cmd[-1]).exists()  # temp capture is always removed


def test_screenshot_reports_screencapture_failure(monkeypatch, screenshot_tmpdir):
    calls = []
    _disable_pyautogui(monkeypatch)
    monkeypatch.setattr(computer, "_PLATFORM", "Darwin")
    monkeypatch.setattr(
        computer, "subprocess",
        _fake_subprocess(calls, returncode=1, stderr="권한 없음".encode()),
    )

    with pytest.raises(ToolError) as excinfo:
        computer.computer_screenshot()

    assert "권한 없음" in str(excinfo.value)
    assert not Path(calls[0][0][-1]).exists()


def test_screenshot_falls_back_to_pyautogui_off_darwin(monkeypatch, screenshot_tmpdir):
    calls = []
    _enable_pyautogui(monkeypatch, calls, size=(1280, 800))
    monkeypatch.setattr(computer, "_PLATFORM", "Linux")

    result = computer.computer_screenshot()

    assert [name for name, _a, _k in calls] == ["screenshot", "save"]
    assert base64.b64decode(result["screenshot_b64"]) == PNG_BYTES
    assert (result["screen_width"], result["screen_height"]) == (1280, 800)


def test_screenshot_without_any_backend_raises(monkeypatch, screenshot_tmpdir):
    _disable_pyautogui(monkeypatch)
    monkeypatch.setattr(computer, "_PLATFORM", "Linux")

    with pytest.raises(ToolError) as excinfo:
        computer.computer_screenshot()

    assert "스크린샷 불가" in str(excinfo.value)


def test_screenshot_survives_temp_cleanup_failure(monkeypatch, screenshot_tmpdir):
    calls = []
    _disable_pyautogui(monkeypatch)
    monkeypatch.setattr(computer, "_PLATFORM", "Darwin")
    monkeypatch.setattr(
        computer, "subprocess",
        _fake_subprocess(calls, writes_png_to_last_arg=True),
    )

    real_os = computer.os

    class _UnlinkFails:
        path = real_os.path

        def unlink(self, _path):
            raise OSError("temp file is locked")

    monkeypatch.setattr(computer, "os", _UnlinkFails())

    result = computer.computer_screenshot()

    assert result["bytes"] == len(PNG_BYTES)
    leftover = Path(calls[0][0][-1])
    assert leftover.exists()  # cleanup failed and was swallowed, not raised
    leftover.unlink()


# ── pointer / keyboard actions ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "action",
    [
        lambda: computer.computer_click(1, 2),
        lambda: computer.computer_type("hi"),
        lambda: computer.computer_key("return"),
        lambda: computer.computer_scroll(1, 2),
        lambda: computer.computer_move(1, 2),
        lambda: computer.computer_drag(1, 2, 3, 4),
    ],
)
def test_actions_refuse_to_run_without_pyautogui(monkeypatch, action):
    _disable_pyautogui(monkeypatch)

    with pytest.raises(ToolError) as excinfo:
        action()

    assert "pyautogui" in str(excinfo.value)


@pytest.mark.parametrize(
    ("kwargs", "expected_call"),
    [
        ({}, "click"),
        ({"double": True}, "doubleClick"),
        ({"button": "right"}, "rightClick"),
        ({"button": "middle"}, "middleClick"),
    ],
)
def test_click_maps_button_and_double_to_pyautogui(monkeypatch, kwargs, expected_call):
    calls = []
    _enable_pyautogui(monkeypatch, calls)

    result = computer.computer_click("10", "20", **kwargs)

    assert calls == [(expected_call, (10, 20), {})]
    assert result["x"] == 10
    assert result["y"] == 20
    assert result["button"] == kwargs.get("button", "left")
    assert result["double"] is kwargs.get("double", False)


def test_type_writes_text_and_reports_length(monkeypatch):
    calls = []
    _enable_pyautogui(monkeypatch, calls)

    result = computer.computer_type("hello", interval="0.5")

    assert calls == [("write", ("hello",), {"interval": 0.5})]
    assert result == {"action": "type", "text": "hello", "chars": 5}


def test_type_truncates_long_text_in_its_echo(monkeypatch):
    calls = []
    _enable_pyautogui(monkeypatch, calls)

    result = computer.computer_type("x" * 100)

    assert result["chars"] == 100
    assert result["text"] == "x" * 60 + "..."


def test_key_sends_hotkeys_and_single_presses(monkeypatch):
    calls = []
    _enable_pyautogui(monkeypatch, calls)

    assert computer.computer_key("command+c") == {"action": "key", "key": "command+c"}
    assert computer.computer_key("escape") == {"action": "key", "key": "escape"}
    assert calls == [
        ("hotkey", ("command", "c"), {}),
        ("press", ("escape",), {}),
    ]


@pytest.mark.parametrize(("direction", "amount"), [("down", -3), ("up", 3)])
def test_scroll_moves_then_scrolls_in_the_requested_direction(monkeypatch, direction, amount):
    calls = []
    _enable_pyautogui(monkeypatch, calls)

    result = computer.computer_scroll(5, 6, direction, 3)

    assert calls == [("moveTo", (5, 6), {}), ("scroll", (amount,), {})]
    assert result == {"action": "scroll", "x": 5, "y": 6, "direction": direction, "clicks": 3}


def test_move_uses_a_short_animation(monkeypatch):
    calls = []
    _enable_pyautogui(monkeypatch, calls)

    assert computer.computer_move(7, 8) == {"action": "move", "x": 7, "y": 8}
    assert calls == [("moveTo", (7, 8), {"duration": 0.2})]


def test_drag_moves_to_the_origin_before_dragging(monkeypatch):
    calls = []
    _enable_pyautogui(monkeypatch, calls)

    result = computer.computer_drag(1, 2, 3, 4)

    assert calls == [
        ("moveTo", (1, 2), {}),
        ("dragTo", (3, 4), {"duration": 0.35, "button": "left"}),
    ]
    assert result == {"action": "drag", "from": [1, 2], "to": [3, 4]}


# ── status / vision ──────────────────────────────────────────────────────────
def test_status_reports_unavailable_without_pyautogui(monkeypatch):
    _disable_pyautogui(monkeypatch)

    assert computer.computer_status() == {
        "available": False,
        "reason": "pyautogui not installed",
    }


def test_status_reports_screen_size_and_failsafe(monkeypatch):
    calls = []
    _enable_pyautogui(monkeypatch, calls, size=(2560, 1440))

    status = computer.computer_status()

    assert status["available"] is True
    assert status["screen_size"] == {"width": 2560, "height": 1440}
    assert status["failsafe"] is True


def test_vision_analyze_rejects_a_too_short_payload():
    with pytest.raises(ToolError) as excinfo:
        computer.vision_analyze("tiny")

    assert "image_b64" in str(excinfo.value)


def test_vision_analyze_truncates_the_echoed_image_and_prompt():
    result = computer.vision_analyze("A" * 200, "  " + "p" * 3000)

    assert result["action"] == "vision_analyze"
    assert result["image_b64"] == "A" * 80 + "..."
    assert result["prompt"] == "p" * 2000
    assert result["supports_vlm"] is True


def test_vision_analyze_keeps_short_images_verbatim():
    payload = "B" * 40

    result = computer.vision_analyze(payload, "")

    assert result["image_b64"] == payload
    assert result["prompt"] == "Describe this image."
