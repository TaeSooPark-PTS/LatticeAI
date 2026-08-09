"""wp04 — installer coverage for latticeai/setup/wizard.py.

Nothing here spawns a real process or opens a real browser: the module's
`asyncio`, `subprocess`, `shutil`, `os` and `platform` references are swapped
for shims, and the process-audit trail is redirected into `tmp_path` through
`LATTICEAI_DATA_DIR` so the assertions can read what the wizard recorded.
"""

import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys

import pytest

from latticeai.services.process_audit import CommandConfirmationError
from latticeai.setup import wizard as setup


class _ModuleShim:
    """Stand-in for an imported module: overrides some names, delegates the rest."""

    def __init__(self, real, **overrides):
        self.__dict__["_real"] = real
        self.__dict__["_overrides"] = overrides

    def __getattr__(self, name):
        overrides = self.__dict__["_overrides"]
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__["_real"], name)


class _FakeProcess:
    def __init__(self, returncode=0, stderr=b"", error=None):
        self.returncode = returncode
        self._stderr = stderr
        self._error = error

    async def communicate(self):
        if self._error is not None:
            raise self._error
        return b"", self._stderr


def _spawner(process, calls):
    async def _create_subprocess_exec(*command, **kwargs):
        calls.append(list(command))
        return process

    return _create_subprocess_exec


async def _no_sleep(delay):
    return None


def _events(items, router=None, **kwargs):
    async def _drain():
        return [chunk async for chunk in setup.install_stream(items, router, **kwargs)]

    parsed = []
    for chunk in asyncio.run(_drain()):
        assert chunk.startswith("data: ")
        assert chunk.endswith("\n\n")
        parsed.append(json.loads(chunk[6:-2]))
    return parsed


def _statuses(events):
    return [event["status"] for event in events]


def _audit_entries(audit_file):
    if not audit_file.exists():
        return []
    return [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture()
def audit_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "audit"))
    return tmp_path / "audit" / "process_audit.jsonl"


@pytest.fixture()
def quick_sleep(monkeypatch):
    monkeypatch.setattr(setup, "asyncio", _ModuleShim(asyncio, sleep=_no_sleep))


# ── _verify_action ────────────────────────────────────────────────────────────

def test_verify_action_reports_missing_python_modules():
    ok, detail = setup._verify_action({"type": "pip", "verify_modules": ["not_a_real_module_wp04", "json"]})

    assert ok is False
    assert "not_a_real_module_wp04" in detail
    assert "json" not in detail


def test_verify_action_derives_modules_from_package_names():
    assert setup._verify_action({"type": "pip", "packages": ["json"]}) == (True, "Python 모듈 import 테스트 통과")


def test_verify_action_delegates_to_the_binary_probe(monkeypatch):
    monkeypatch.setattr(setup, "_verify_binary", lambda binary: (True, "ollama " + binary))

    assert setup._verify_action({"type": "url", "binary": "ollama"}) == (True, "ollama ollama")


def test_verify_action_accepts_actions_with_nothing_to_check():
    assert setup._verify_action({"type": "auth", "url": "https://example.test"}) == (True, "검증 항목 없음")


# ── _repair_action ────────────────────────────────────────────────────────────

def test_repair_action_reports_a_path_fix(monkeypatch):
    repaired = []
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: repaired.append(binary) or [])
    monkeypatch.setattr(setup, "_verify_binary", lambda binary: (True, "ollama 1.2.3"))

    ok, detail = asyncio.run(setup._repair_action({"type": "url", "binary": "ollama"}))

    assert ok is True
    assert detail == "PATH 자동 보정 완료: ollama 1.2.3"
    assert repaired == ["ollama"]


def test_repair_action_refuses_a_pip_retry_without_a_matching_token(monkeypatch):
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: [])
    monkeypatch.setattr(setup, "_verify_binary", lambda binary: (False, "missing"))

    ok, detail = asyncio.run(
        setup._repair_action(
            {"type": "pip", "packages": ["mlx-vlm"], "binary": "mlx"},
            confirmation_token="stale-token",
        )
    )

    assert ok is False
    assert detail == "설치 명령 확인 토큰이 일치하지 않습니다."


def test_repair_action_reinstalls_pip_packages_with_a_valid_token(monkeypatch):
    action = {"type": "pip", "packages": ["mlx-vlm", "vllm"]}
    token = setup._action_command_plan(action, name="repair_action")["confirmation_token"]
    calls = []

    async def _install(package, *, confirmed=False, actor=None):
        calls.append((package, confirmed, actor))
        return True, ""

    monkeypatch.setattr(setup, "_pip_install", _install)
    monkeypatch.setattr(setup, "_verify_action", lambda action_: (True, "import ok"))

    assert asyncio.run(
        setup._repair_action(action, confirmation_token=token, actor="dev@example.test")
    ) == (True, "import ok")
    assert calls == [("mlx-vlm", True, "dev@example.test"), ("vllm", True, "dev@example.test")]


def test_repair_action_stops_at_the_first_failed_reinstall(monkeypatch):
    action = {"type": "pip", "packages": ["mlx-vlm", "vllm"]}
    token = setup._action_command_plan(action, name="repair_action")["confirmation_token"]
    calls = []

    async def _install(package, *, confirmed=False, actor=None):
        calls.append(package)
        return False, "wheel build failed"

    monkeypatch.setattr(setup, "_pip_install", _install)

    assert asyncio.run(setup._repair_action(action, confirmation_token=token)) == (False, "wheel build failed")
    assert calls == ["mlx-vlm"]


def test_repair_action_gives_up_when_there_is_no_recovery_route():
    assert asyncio.run(setup._repair_action({"type": "auth", "url": "https://example.test"})) == (
        False,
        "자동 복구 방법을 찾지 못했습니다.",
    )
    assert asyncio.run(setup._repair_action({"type": "pip", "packages": []})) == (
        False,
        "자동 복구 방법을 찾지 못했습니다.",
    )


# ── install_stream ────────────────────────────────────────────────────────────

def test_install_stream_skips_items_that_need_no_action(quick_sleep):
    events = _events(
        [
            {"id": "engine_mlx", "name": "MLX", "action": None},
            {"id": "component_git", "name": "Git"},
            {"name": "no id at all"},
        ]
    )

    assert _statuses(events) == ["skipped", "skipped", "skipped", "complete"]
    assert events[0]["msg"] == "MLX — 이미 준비됨"
    assert events[2]["id"] == "unknown"


def test_install_stream_installs_pip_packages_and_verifies_them(monkeypatch):
    action = setup._attach_action_plan(
        {"type": "pip", "packages": ["mlx-vlm", "vllm"], "verify_modules": ["mlx"]},
        name="engine_mlx",
    )
    calls = []

    async def _install(package, *, confirmed=False, actor=None):
        calls.append((package, confirmed, actor))
        return True, ""

    monkeypatch.setattr(setup, "_pip_install", _install)
    monkeypatch.setattr(setup, "_verify_action", lambda action_: (True, "import ok"))

    events = _events(
        [{"id": "engine_mlx", "name": "MLX", "action": action}],
        user_email="dev@example.test",
    )

    assert _statuses(events) == [
        "starting",
        "running",
        "progress",
        "running",
        "progress",
        "running",
        "done",
        "complete",
    ]
    assert calls == [("mlx-vlm", True, "dev@example.test"), ("vllm", True, "dev@example.test")]
    assert "import ok" in events[-2]["msg"]


def test_install_stream_refuses_a_pip_action_with_a_stale_token(monkeypatch):
    def _never(*args, **kwargs):
        raise AssertionError("no package may be installed on a stale token")

    monkeypatch.setattr(setup, "_pip_install", _never)

    events = _events(
        [
            {
                "id": "engine_mlx",
                "name": "MLX",
                "action": {"type": "pip", "packages": ["mlx-vlm"], "confirmation_token": "stale"},
            }
        ]
    )

    assert _statuses(events) == ["starting", "error", "complete"]
    assert events[1]["msg"] == "설치 명령 확인 토큰이 일치하지 않습니다."


def test_install_stream_stops_at_the_first_failing_package(monkeypatch):
    action = setup._attach_action_plan({"type": "pip", "packages": ["mlx-vlm", "vllm"]}, name="engine_mlx")
    attempted = []

    async def _install(package, *, confirmed=False, actor=None):
        attempted.append(package)
        return False, "compiler not found " + "x" * 500

    monkeypatch.setattr(setup, "_pip_install", _install)

    events = _events([{"id": "engine_mlx", "name": "MLX", "action": action}])

    assert _statuses(events) == ["starting", "running", "error", "complete"]
    assert attempted == ["mlx-vlm"]
    assert len(events[2]["msg"]) < 460


def test_install_stream_repairs_a_failed_pip_verification(monkeypatch):
    action = setup._attach_action_plan({"type": "pip", "packages": ["mlx-vlm"]}, name="engine_mlx")
    repairs = []

    async def _install(package, *, confirmed=False, actor=None):
        return True, ""

    async def _repair(action_, *, confirmation_token=None, actor=None):
        repairs.append((confirmation_token, actor))
        return True, "PATH 자동 보정 완료"

    monkeypatch.setattr(setup, "_pip_install", _install)
    monkeypatch.setattr(setup, "_verify_action", lambda action_: (False, "모듈 없음"))
    monkeypatch.setattr(setup, "_repair_action", _repair)

    events = _events([{"id": "engine_mlx", "name": "MLX", "action": action}], user_email="dev@example.test")

    assert _statuses(events) == ["starting", "running", "progress", "running", "running", "done", "complete"]
    assert repairs == [(action["confirmation_token"], "dev@example.test")]


def test_install_stream_reports_an_unrecoverable_pip_verification(monkeypatch):
    action = setup._attach_action_plan({"type": "pip", "packages": ["mlx-vlm"]}, name="engine_mlx")

    async def _install(package, *, confirmed=False, actor=None):
        return True, ""

    async def _repair(action_, *, confirmation_token=None, actor=None):
        return False, "자동 복구 방법을 찾지 못했습니다."

    monkeypatch.setattr(setup, "_pip_install", _install)
    monkeypatch.setattr(setup, "_verify_action", lambda action_: (False, "모듈 없음"))
    monkeypatch.setattr(setup, "_repair_action", _repair)

    events = _events([{"id": "engine_mlx", "name": "MLX", "action": action}])

    assert _statuses(events)[-2:] == ["error", "complete"]


def test_install_stream_refuses_a_brew_action_with_a_stale_token(monkeypatch):
    called = []

    async def _brew(package, *, confirmed=False, actor=None):
        called.append(package)
        return True, ""

    monkeypatch.setattr(setup, "_brew_install", _brew)

    events = _events(
        [
            {
                "id": "engine_ollama",
                "name": "Ollama",
                "action": {"type": "brew", "package": "ollama", "confirmation_token": "stale"},
            }
        ]
    )

    assert _statuses(events) == ["starting", "error", "complete"]
    assert events[1]["msg"] == "설치 명령 확인 토큰이 일치하지 않습니다."
    assert called == []


def test_install_stream_installs_via_brew_and_repairs_the_path(monkeypatch):
    action = setup._attach_action_plan(
        {"type": "brew", "package": "ollama", "binary": "ollama"},
        name="engine_ollama",
    )
    repaired = []
    calls = []

    async def _brew(package, *, confirmed=False, actor=None):
        calls.append((package, confirmed, actor))
        return True, ""

    monkeypatch.setattr(setup, "_brew_install", _brew)
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: repaired.append(binary) or [])
    monkeypatch.setattr(setup, "_verify_action", lambda action_: (True, "ollama 1.2.3"))

    events = _events(
        [{"id": "engine_ollama", "name": "Ollama", "action": action}],
        user_email="dev@example.test",
    )

    assert _statuses(events) == ["starting", "running", "running", "done", "complete"]
    assert calls == [("ollama", True, "dev@example.test")]
    assert repaired == ["ollama"]
    assert "ollama 1.2.3" in events[-2]["msg"]


def test_install_stream_repairs_a_failed_brew_verification(monkeypatch):
    action = setup._attach_action_plan({"type": "brew", "package": "ollama"}, name="engine_ollama")

    async def _brew(package, *, confirmed=False, actor=None):
        return True, ""

    async def _repair(action_, *, confirmation_token=None, actor=None):
        return False, "자동 복구 방법을 찾지 못했습니다."

    monkeypatch.setattr(setup, "_brew_install", _brew)
    monkeypatch.setattr(setup, "_verify_action", lambda action_: (False, "감지 실패"))
    monkeypatch.setattr(setup, "_repair_action", _repair)

    events = _events([{"id": "engine_ollama", "name": "Ollama", "action": action}])

    assert _statuses(events) == ["starting", "running", "running", "running", "error", "complete"]


def test_install_stream_opens_the_official_page_when_brew_fails(monkeypatch):
    action = setup._attach_action_plan(
        {"type": "brew", "package": "ollama", "official_url": setup.OFFICIAL_DOWNLOADS["ollama"]},
        name="engine_ollama",
    )
    opened = []

    async def _brew(package, *, confirmed=False, actor=None):
        return False, "brew: command failed"

    monkeypatch.setattr(setup, "_brew_install", _brew)
    monkeypatch.setattr(setup, "open_url", opened.append)

    events = _events([{"id": "engine_ollama", "name": "Ollama", "action": action}])

    assert _statuses(events) == ["starting", "running", "auth", "error", "complete"]
    assert events[2]["auth_url"] == setup.OFFICIAL_DOWNLOADS["ollama"]
    assert opened == [setup.OFFICIAL_DOWNLOADS["ollama"]]


def test_install_stream_reports_a_brew_failure_without_a_download_page(monkeypatch):
    action = setup._attach_action_plan({"type": "brew", "package": "ollama"}, name="engine_ollama")
    opened = []

    async def _brew(package, *, confirmed=False, actor=None):
        return False, "brew: command failed"

    monkeypatch.setattr(setup, "_brew_install", _brew)
    monkeypatch.setattr(setup, "open_url", opened.append)

    events = _events([{"id": "engine_ollama", "name": "Ollama", "action": action}])

    assert _statuses(events) == ["starting", "running", "error", "complete"]
    assert opened == []


def test_install_stream_loads_a_model_through_the_router():
    loaded = []

    class _Router:
        async def load_model(self, model_id):
            loaded.append(model_id)
            return {"ok": True}

    events = _events(
        [
            {
                "id": "model_demo",
                "name": "Qwen3-VL 4B",
                "action": {"type": "load_model", "model_id": "mlx-community/Qwen3-VL-4B-Instruct-4bit"},
            }
        ],
        router=_Router(),
    )

    assert _statuses(events) == ["starting", "running", "done", "complete"]
    assert loaded == ["mlx-community/Qwen3-VL-4B-Instruct-4bit"]


def test_install_stream_reports_a_model_load_failure():
    class _Router:
        async def load_model(self, model_id):
            raise RuntimeError("out of memory")

    events = _events(
        [{"id": "model_demo", "name": "Qwen3-VL 4B", "action": {"type": "load_model", "model_id": "demo"}}],
        router=_Router(),
    )

    assert _statuses(events) == ["starting", "running", "error", "complete"]
    assert "out of memory" in events[2]["msg"]


def test_install_stream_opens_an_auth_page(monkeypatch):
    opened = []
    monkeypatch.setattr(setup, "open_url", opened.append)

    events = _events(
        [
            {
                "id": "mcp_github",
                "name": "GitHub",
                "action": {"type": "auth", "url": "https://github.com/apps", "mcp_id": "github"},
            }
        ]
    )

    assert _statuses(events) == ["starting", "auth", "waiting", "complete"]
    assert events[1]["auth_url"] == "https://github.com/apps"
    assert opened == ["https://github.com/apps"]


def test_install_stream_waits_for_a_binary_after_opening_a_download_page(monkeypatch):
    opened = []
    repaired = []

    async def _wait(binary, seconds=300):
        return True, binary + " 1.2.3"

    monkeypatch.setattr(setup, "open_url", opened.append)
    monkeypatch.setattr(setup, "_wait_for_binary", _wait)
    monkeypatch.setattr(setup, "repair_path_for", lambda binary=None: repaired.append(binary) or [])

    events = _events(
        [
            {
                "id": "engine_lmstudio",
                "name": "LM Studio",
                "action": {"type": "url", "url": setup.OFFICIAL_DOWNLOADS["lmstudio"], "binary": "lms"},
            }
        ]
    )

    assert _statuses(events) == ["starting", "auth", "waiting", "done", "complete"]
    assert opened == [setup.OFFICIAL_DOWNLOADS["lmstudio"]]
    assert repaired == ["lms"]
    assert "lms 1.2.3" in events[3]["msg"]


def test_install_stream_reports_a_binary_that_never_appears(monkeypatch):
    async def _wait(binary, seconds=300):
        return False, binary + " 설치 완료를 제한 시간 안에 감지하지 못했습니다."

    monkeypatch.setattr(setup, "open_url", lambda url: None)
    monkeypatch.setattr(setup, "_wait_for_binary", _wait)

    events = _events(
        [{"id": "component_git", "name": "Git", "action": {"type": "url", "url": "https://git-scm.com", "binary": "git"}}]
    )

    assert _statuses(events) == ["starting", "auth", "waiting", "error", "complete"]
    assert "공식 페이지" in events[3]["msg"]


def test_install_stream_url_without_a_binary_just_waits(monkeypatch):
    opened = []
    monkeypatch.setattr(setup, "open_url", opened.append)

    events = _events(
        [{"id": "component_homebrew", "name": "Homebrew", "action": {"type": "url", "url": "https://brew.sh"}}]
    )

    assert _statuses(events) == ["starting", "auth", "waiting", "complete"]
    assert events[2]["msg"] == "브라우저에서 설치 또는 인증을 완료한 뒤 다시 시도하세요"
    assert opened == ["https://brew.sh"]


def test_install_stream_reports_unknown_action_types():
    events = _events([{"id": "mystery", "name": "Mystery", "action": {"type": "telepathy"}}])

    assert _statuses(events) == ["starting", "error", "complete"]
    assert events[1]["msg"] == "알 수 없는 액션: telepathy"
    assert events[-1]["msg"] == "모든 항목 처리 완료!"


# ── _pip_install ──────────────────────────────────────────────────────────────

def test_pip_install_records_a_successful_run(audit_file, monkeypatch):
    calls = []
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(asyncio, create_subprocess_exec=_spawner(_FakeProcess(returncode=0), calls)),
    )

    assert asyncio.run(setup._pip_install("demo-package", confirmed=True, actor="dev@example.test")) == (True, "")
    assert calls == [[sys.executable, "-m", "pip", "install", "--upgrade", "demo-package"]]

    entries = _audit_entries(audit_file)
    assert [entry["status"] for entry in entries] == ["started", "finished"]
    assert entries[1]["returncode"] == 0
    assert entries[1]["user_email"] == "dev@example.test"
    assert entries[0]["plan"]["name"] == "pip:demo-package"


def test_pip_install_returns_stderr_on_a_non_zero_exit(audit_file, monkeypatch):
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(asyncio, create_subprocess_exec=_spawner(_FakeProcess(returncode=1, stderr=b"no matching distribution"), [])),
    )

    assert asyncio.run(setup._pip_install("demo-package", confirmed=True)) == (False, "no matching distribution")
    assert _audit_entries(audit_file)[-1]["returncode"] == 1


def test_pip_install_accepts_a_matching_confirmation_token(audit_file, monkeypatch):
    command = [sys.executable, "-m", "pip", "install", "--upgrade", "demo-package"]
    token = setup.command_plan(command, name="pip:demo-package", purpose="setup_wizard_install")["confirmation_token"]
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(asyncio, create_subprocess_exec=_spawner(_FakeProcess(returncode=0), [])),
    )

    assert asyncio.run(setup._pip_install("demo-package", confirmation_token=token)) == (True, "")
    assert [entry["status"] for entry in _audit_entries(audit_file)] == ["started", "finished"]


def test_pip_install_refuses_to_run_without_confirmation(audit_file, monkeypatch):
    def _never(*args, **kwargs):
        raise AssertionError("no process may be spawned without confirmation")

    monkeypatch.setattr(setup, "asyncio", _ModuleShim(asyncio, create_subprocess_exec=_never))

    ok, detail = asyncio.run(setup._pip_install("demo-package"))

    assert ok is False
    assert "confirmation token" in detail
    entries = _audit_entries(audit_file)
    assert [entry["status"] for entry in entries] == ["denied"]
    assert entries[0]["error"]


def test_pip_install_reports_a_timeout(audit_file, monkeypatch):
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(
            asyncio,
            create_subprocess_exec=_spawner(_FakeProcess(error=asyncio.TimeoutError()), []),
        ),
    )

    assert asyncio.run(setup._pip_install("demo-package", confirmed=True)) == (False, "설치 시간 초과 (10분)")
    assert [entry["status"] for entry in _audit_entries(audit_file)] == ["started", "timeout"]


def test_pip_install_reports_an_unexpected_failure(audit_file, monkeypatch):
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(asyncio, create_subprocess_exec=_spawner(_FakeProcess(error=OSError("exec format error")), [])),
    )

    assert asyncio.run(setup._pip_install("demo-package", confirmed=True)) == (False, "exec format error")
    entries = _audit_entries(audit_file)
    assert [entry["status"] for entry in entries] == ["started", "error"]
    assert "exec format error" in entries[1]["error"]


# ── _brew_install ─────────────────────────────────────────────────────────────

def test_brew_install_requires_homebrew(monkeypatch):
    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, which=lambda binary: None))

    ok, detail = asyncio.run(setup._brew_install("ollama", confirmed=True))

    assert ok is False
    assert "brew.sh" in detail


def test_brew_install_records_a_successful_run(audit_file, monkeypatch):
    calls = []
    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, which=lambda binary: "/opt/homebrew/bin/brew"))
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(asyncio, create_subprocess_exec=_spawner(_FakeProcess(returncode=0), calls)),
    )

    assert asyncio.run(setup._brew_install("ollama", confirmed=True, actor="dev@example.test")) == (True, "")
    assert calls == [["/opt/homebrew/bin/brew", "install", "ollama"]]

    entries = _audit_entries(audit_file)
    assert [entry["status"] for entry in entries] == ["started", "finished"]
    assert entries[0]["plan"]["name"] == "brew:ollama"


def test_brew_install_returns_stderr_on_a_non_zero_exit(audit_file, monkeypatch):
    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, which=lambda binary: "/opt/homebrew/bin/brew"))
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(asyncio, create_subprocess_exec=_spawner(_FakeProcess(returncode=1, stderr=b"No available formula"), [])),
    )

    assert asyncio.run(setup._brew_install("ollama", confirmed=True)) == (False, "No available formula")
    assert _audit_entries(audit_file)[-1]["returncode"] == 1


def test_brew_install_refuses_to_run_without_confirmation(audit_file, monkeypatch):
    def _never(*args, **kwargs):
        raise AssertionError("no process may be spawned without confirmation")

    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, which=lambda binary: "/opt/homebrew/bin/brew"))
    monkeypatch.setattr(setup, "asyncio", _ModuleShim(asyncio, create_subprocess_exec=_never))

    ok, detail = asyncio.run(setup._brew_install("ollama"))

    assert ok is False
    assert "confirmation token" in detail
    assert [entry["status"] for entry in _audit_entries(audit_file)] == ["denied"]


def test_brew_install_accepts_a_matching_confirmation_token(audit_file, monkeypatch):
    token = setup.command_plan(
        ["/opt/homebrew/bin/brew", "install", "ollama"],
        name="brew:ollama",
        purpose="setup_wizard_install",
    )["confirmation_token"]
    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, which=lambda binary: "/opt/homebrew/bin/brew"))
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(asyncio, create_subprocess_exec=_spawner(_FakeProcess(returncode=0), [])),
    )

    assert asyncio.run(setup._brew_install("ollama", confirmation_token=token)) == (True, "")
    assert [entry["status"] for entry in _audit_entries(audit_file)] == ["started", "finished"]


def test_brew_install_reports_a_timeout(audit_file, monkeypatch):
    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, which=lambda binary: "/opt/homebrew/bin/brew"))
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(asyncio, create_subprocess_exec=_spawner(_FakeProcess(error=asyncio.TimeoutError()), [])),
    )

    assert asyncio.run(setup._brew_install("ollama", confirmed=True)) == (False, "설치 시간 초과 (5분)")
    assert [entry["status"] for entry in _audit_entries(audit_file)] == ["started", "timeout"]


def test_brew_install_reports_an_unexpected_failure(audit_file, monkeypatch):
    monkeypatch.setattr(setup, "shutil", _ModuleShim(shutil, which=lambda binary: "/opt/homebrew/bin/brew"))
    monkeypatch.setattr(
        setup,
        "asyncio",
        _ModuleShim(asyncio, create_subprocess_exec=_spawner(_FakeProcess(error=OSError("disk full")), [])),
    )

    assert asyncio.run(setup._brew_install("ollama", confirmed=True)) == (False, "disk full")
    assert [entry["status"] for entry in _audit_entries(audit_file)] == ["started", "error"]


def test_confirmation_error_is_the_documented_type():
    assert issubclass(CommandConfirmationError, ValueError)


# ── open_url ──────────────────────────────────────────────────────────────────

def test_open_url_uses_open_on_macos(audit_file, monkeypatch):
    spawned = []
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    monkeypatch.setattr(setup, "subprocess", _ModuleShim(subprocess, Popen=spawned.append))

    setup.open_url("https://brew.sh")

    assert spawned == [["open", "https://brew.sh"]]
    entries = _audit_entries(audit_file)
    assert [entry["status"] for entry in entries] == ["started", "spawned"]
    assert entries[0]["plan"]["command_preview"] == ["open", "https://brew.sh"]


def test_open_url_uses_startfile_on_windows(audit_file, monkeypatch):
    started = []
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Windows"))
    monkeypatch.setattr(setup, "os", _ModuleShim(os, startfile=started.append))

    setup.open_url("https://ollama.com/download")

    assert started == ["https://ollama.com/download"]
    entries = _audit_entries(audit_file)
    assert [entry["status"] for entry in entries] == ["started", "spawned"]
    assert entries[0]["plan"]["command_preview"] == ["os.startfile", "https://ollama.com/download"]


def test_open_url_uses_xdg_open_elsewhere(audit_file, monkeypatch):
    spawned = []
    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    monkeypatch.setattr(setup, "subprocess", _ModuleShim(subprocess, Popen=spawned.append))

    setup.open_url("https://lmstudio.ai/download")

    assert spawned == [["xdg-open", "https://lmstudio.ai/download"]]
    assert [entry["status"] for entry in _audit_entries(audit_file)] == ["started", "spawned"]


def test_open_url_records_a_spawn_failure(audit_file, monkeypatch):
    def _boom(command):
        raise FileNotFoundError("xdg-open missing")

    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Linux"))
    monkeypatch.setattr(setup, "subprocess", _ModuleShim(subprocess, Popen=_boom))

    setup.open_url("https://lmstudio.ai/download")

    entries = _audit_entries(audit_file)
    assert [entry["status"] for entry in entries] == ["started", "error"]
    assert "xdg-open missing" in entries[1]["error"]


def test_open_url_stays_silent_when_auditing_itself_fails(audit_file, monkeypatch):
    def _audit_boom(*args, **kwargs):
        raise RuntimeError("audit disk full")

    def _never(command):
        raise AssertionError("the browser must not be launched after the audit failed")

    monkeypatch.setattr(setup, "platform", _ModuleShim(platform, system=lambda: "Darwin"))
    monkeypatch.setattr(setup, "subprocess", _ModuleShim(subprocess, Popen=_never))
    monkeypatch.setattr(setup, "append_process_audit_event", _audit_boom)

    setup.open_url("https://brew.sh")

    assert _audit_entries(audit_file) == []
