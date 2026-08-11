"""model_engines install + pull — the two paths that shell out on a user's behalf.

Both are audited: every attempt appends to the process-audit log, and the
install refuses to run at all without a confirmation token matching the exact
command. The tests assert that trail (``LATTICEAI_DATA_DIR`` points at
``tmp_path``, so the log is a real file this test wrote and read back), and they
never launch a process — ``model_engines.subprocess`` is replaced wholesale.
"""

from __future__ import annotations

import json
import subprocess
import types

import pytest

from latticeai.services import model_engines, model_runtime
from latticeai.services.model_runtime import cloud as mr_cloud
from latticeai.services.model_runtime import download as mr_download
from latticeai.services.model_runtime import engines as mr_engines
from latticeai.services.model_runtime import loading as mr_loading
from latticeai.services.model_runtime import service as mr_service
from latticeai.services.model_runtime import state as mr_state
from latticeai.services.model_runtime import status as mr_status

# ── v11.3.0 split shim ────────────────────────────────────────────────────────
# ``latticeai/services/model_runtime.py`` became a package (state / engines /
# download / status / loading / cloud / service). Reading a name through the
# package still works, so the calls below are unchanged — but *patching* a name
# on the package ``__init__`` does not reach a submodule's own global. Every
# stub is therefore installed on every module that binds the name, which is
# exactly the one binding the single-file module used to have.
_RUNTIME_MODULES = (
    model_runtime,
    mr_cloud,
    mr_download,
    mr_engines,
    mr_loading,
    mr_service,
    mr_state,
    mr_status,
)


def _patch_runtime(monkeypatch, name, value):
    targets = [module for module in _RUNTIME_MODULES if hasattr(module, name)]
    assert targets, f"no model_runtime module binds {name!r}"
    for module in targets:
        monkeypatch.setattr(module, name, value)
from latticeai.services.model_errors import ModelRuntimeError


class _PullProcess:
    """``ollama pull`` without ollama: a scripted stdout and an exit code."""

    def __init__(self, lines, *, returncode: int = 0, explode: bool = False) -> None:
        self.stdout = self._stream(lines, explode)
        self._returncode = returncode
        self.killed = False

    @staticmethod
    def _stream(lines, explode):
        for line in lines:
            yield line
        if explode:
            raise OSError("pipe died mid-download")

    def wait(self) -> int:
        return self._returncode

    def kill(self) -> None:
        self.killed = True


def _install_subprocess(monkeypatch, *, run=None, popen=None) -> dict:
    calls: dict = {"run": [], "popen": []}

    def _run(command, **kwargs):
        calls["run"].append((list(command), kwargs))
        if run is None:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return run(list(command), **kwargs)

    def _popen(command, **kwargs):
        calls["popen"].append((list(command), kwargs))
        if popen is None:
            return _PullProcess([])
        return popen(list(command), **kwargs)

    monkeypatch.setattr(
        model_engines,
        "subprocess",
        types.SimpleNamespace(
            run=_run,
            Popen=_popen,
            DEVNULL=subprocess.DEVNULL,
            PIPE=subprocess.PIPE,
            STDOUT=subprocess.STDOUT,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    return calls


@pytest.fixture
def audit_log(monkeypatch, tmp_path):
    """Point the process audit at tmp_path and read it back as events."""
    monkeypatch.setenv("LATTICEAI_DATA_DIR", str(tmp_path / "data"))

    def _events():
        path = tmp_path / "data" / "process_audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    return _events


# ── install_engine ───────────────────────────────────────────────────────────


def test_install_engine_records_the_run_and_reports_the_outcome(monkeypatch, tmp_path, audit_log):
    _patch_runtime(monkeypatch, "engine_installed", lambda engine: True)
    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)
    calls = _install_subprocess(
        monkeypatch,
        run=lambda command, **kwargs: types.SimpleNamespace(
            returncode=0, stdout="installed mlx", stderr=""
        ),
    )
    plan = model_engines.engine_install_plan("local_mlx", base_dir=tmp_path)

    result = model_engines.install_engine(
        "local_mlx", plan["confirmation_token"], base_dir=tmp_path
    )

    assert result["engine"] == "local_mlx"
    assert result["returncode"] == 0
    assert result["stdout"] == "installed mlx"
    assert result["installed"] is True
    assert result["command_hash"] == plan["command_hash"]
    assert calls["run"][0][1]["cwd"] == str(tmp_path)
    assert [e["status"] for e in audit_log()] == ["started", "finished"]


def test_install_engine_reports_a_timeout_as_a_408(monkeypatch, tmp_path, audit_log):
    def _timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=900)

    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)
    _install_subprocess(monkeypatch, run=_timeout)
    plan = model_engines.engine_install_plan("local_mlx", base_dir=tmp_path)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.install_engine("local_mlx", plan["confirmation_token"], base_dir=tmp_path)

    assert err.value.status_code == 408
    assert [e["status"] for e in audit_log()] == ["started", "timeout"]


def test_install_engine_records_an_unexpected_failure_before_re_raising(
    monkeypatch, tmp_path, audit_log
):
    def _boom(command, **kwargs):
        raise OSError("no such file")

    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)
    _install_subprocess(monkeypatch, run=_boom)
    plan = model_engines.engine_install_plan("local_mlx", base_dir=tmp_path)

    with pytest.raises(OSError, match="no such file"):
        model_engines.install_engine("local_mlx", plan["confirmation_token"], base_dir=tmp_path)

    events = audit_log()
    assert [e["status"] for e in events] == ["started", "error"]
    assert "no such file" in events[-1]["error"]


def test_installing_ollama_leaves_a_running_daemon_alone(monkeypatch, tmp_path, audit_log):
    _patch_runtime(monkeypatch, "engine_installed", lambda engine: True)
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    monkeypatch.setattr(
        model_engines, "shutil", types.SimpleNamespace(which=lambda name: "/bin/brew")
    )
    calls = _install_subprocess(monkeypatch)
    plan = model_engines.engine_install_plan("ollama", base_dir=tmp_path)

    result = model_engines.install_engine("ollama", plan["confirmation_token"], base_dir=tmp_path)

    assert result["daemon_started"] == "already_running"
    assert calls["run"][0][0] == ["brew", "install", "ollama"]
    assert calls["run"][1][0] == ["/bin/ollama", "list"]
    assert calls["popen"] == []


def test_installing_ollama_starts_the_daemon_when_the_probe_fails(monkeypatch, tmp_path, audit_log):
    attempts = {"n": 0}

    def _run(command, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 2:
            raise OSError("daemon not up")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    _patch_runtime(monkeypatch, "engine_installed", lambda engine: True)
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    monkeypatch.setattr(
        model_engines, "shutil", types.SimpleNamespace(which=lambda name: "/bin/brew")
    )
    calls = _install_subprocess(monkeypatch, run=_run)
    plan = model_engines.engine_install_plan("ollama", base_dir=tmp_path)

    result = model_engines.install_engine("ollama", plan["confirmation_token"], base_dir=tmp_path)

    assert result["daemon_started"] is True
    assert calls["popen"][0][0] == ["/bin/ollama", "serve"]
    statuses = [(e["event_type"], e["status"]) for e in audit_log()]
    assert statuses == [
        ("engine_install", "started"),
        ("engine_install", "finished"),
        ("engine_daemon_start", "started"),
        ("engine_daemon_start", "spawned"),
    ]


def test_a_daemon_that_will_not_spawn_is_reported_not_raised(monkeypatch, tmp_path, audit_log):
    def _run(command, **kwargs):
        return types.SimpleNamespace(returncode=0 if command[0] == "brew" else 1, stdout="", stderr="")

    def _popen(command, **kwargs):
        raise OSError("permission denied")

    _patch_runtime(monkeypatch, "engine_installed", lambda engine: True)
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    monkeypatch.setattr(
        model_engines, "shutil", types.SimpleNamespace(which=lambda name: "/bin/brew")
    )
    _install_subprocess(monkeypatch, run=_run, popen=_popen)
    plan = model_engines.engine_install_plan("ollama", base_dir=tmp_path)

    result = model_engines.install_engine("ollama", plan["confirmation_token"], base_dir=tmp_path)

    assert result["daemon_started"] is False
    assert [(e["event_type"], e["status"]) for e in audit_log()][-1] == (
        "engine_daemon_start",
        "error",
    )


# ── pull_ollama_model_with_progress ──────────────────────────────────────────


def test_pulling_without_ollama_is_a_400(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.pull_ollama_model_with_progress("qwen3:8b")

    assert err.value.status_code == 400


def test_pull_progress_tracks_percentages_and_finishes_at_100(monkeypatch):
    emitted: list = []
    lines = [
        "pulling manifest\n",
        "pulling 1a2b:  45% ▕███  ▏\rpulling 1a2b:  90.5% ▕████▏\n",
        "\n",
        "success\n",
    ]
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    calls = _install_subprocess(monkeypatch, popen=lambda command, **kwargs: _PullProcess(lines))

    result = model_engines.pull_ollama_model_with_progress("qwen3:8b", emitted.append)

    assert result == {"provider": "ollama", "model": "qwen3:8b", "returncode": 0}
    assert calls["popen"][0][0] == ["/bin/ollama", "pull", "qwen3:8b"]
    percents = [p.get("percent") for p in emitted]
    assert (percents[0], emitted[0]["indeterminate"]) == (0, True)  # opening frame
    assert 45.0 in percents
    assert 90.5 in percents
    assert percents[-1] == 100
    assert emitted[-1]["detail"] == "qwen3:8b"
    # the non-percentage line still reports, as indeterminate until the first %
    assert emitted[1]["indeterminate"] is True
    assert emitted[1]["detail"] == "pulling manifest"


def test_pull_works_without_a_progress_listener(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    _install_subprocess(
        monkeypatch, popen=lambda command, **kwargs: _PullProcess(["pulling manifest\n"])
    )

    assert model_engines.pull_ollama_model_with_progress("qwen3:8b")["returncode"] == 0


def test_a_failed_pull_raises_with_the_tail_of_the_output(monkeypatch):
    lines = [f"line {n}\n" for n in range(20)] + ["Error: model not found\n"]
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    _install_subprocess(
        monkeypatch, popen=lambda command, **kwargs: _PullProcess(lines, returncode=1)
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.pull_ollama_model_with_progress("nope:1b")

    assert err.value.status_code == 500
    assert "Error: model not found" in str(err.value.detail)
    assert "line 0" not in str(err.value.detail)  # only the last 12 lines


def test_a_broken_pull_stream_kills_the_child_process(monkeypatch):
    process = _PullProcess(["pulling manifest\n"], explode=True)
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    _install_subprocess(monkeypatch, popen=lambda command, **kwargs: process)

    with pytest.raises(OSError, match="pipe died"):
        model_engines.pull_ollama_model_with_progress("qwen3:8b")

    assert process.killed is True


# ── get_ollama_pulled_models ─────────────────────────────────────────────────


def test_pulled_models_are_empty_without_the_binary(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)

    assert model_engines.get_ollama_pulled_models() == set()


def test_pulled_models_are_parsed_from_the_list_table(monkeypatch):
    table = "NAME               ID        SIZE\nqwen3:8b  abc  5.2 GB\ngemma3:4b  def  3.3 GB\n\n"
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    _install_subprocess(
        monkeypatch,
        run=lambda command, **kwargs: types.SimpleNamespace(returncode=0, stdout=table, stderr=""),
    )

    assert model_engines.get_ollama_pulled_models() == {"qwen3:8b", "gemma3:4b"}


def test_pulled_models_are_empty_when_the_listing_fails(monkeypatch):
    def _boom(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=5)

    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    _install_subprocess(monkeypatch, run=_boom)

    assert model_engines.get_ollama_pulled_models() == set()
