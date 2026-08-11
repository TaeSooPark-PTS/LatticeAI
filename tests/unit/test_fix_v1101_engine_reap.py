"""v11.0.1 D5 — a previous engine server is reaped, or the caller is told.

``ensure_vllm_server`` terminated the process it was replacing, waited ten
seconds, and on timeout called ``kill()`` — with no ``wait()`` after it. On
POSIX a killed child stays a zombie until somebody reaps it, so ``poll()``
kept returning ``None``; the recheck that followed read that as "a server is
already running" and returned ``None`` to the caller. The user asked to load a
model, got a success, and no server had been started.

The fake below models the real semantics that made this a defect: ``kill()``
delivers the signal, but only ``wait()`` turns the process into one whose
``poll()`` answers. ``_reap_local_server`` now waits after the kill and raises
a 409 if the process is still there afterwards.
"""

from __future__ import annotations

import subprocess
import sys
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


class _PreviousServer:
    """The engine server that was running before this request arrived."""

    def __init__(self, *, exits_on_terminate: bool = True, exits_on_kill: bool = True) -> None:
        self._exits_on_terminate = exits_on_terminate
        self._exits_on_kill = exits_on_kill
        self._exited = False
        self._reaped = False
        self.terminated = False
        self.killed = False
        self.waits: list = []

    def poll(self):
        # A signalled-but-unreaped child still reports "running".
        return 0 if self._reaped else None

    def terminate(self) -> None:
        self.terminated = True
        self._exited = self._exited or self._exits_on_terminate

    def kill(self) -> None:
        self.killed = True
        self._exited = self._exited or self._exits_on_kill

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if not self._exited:
            raise subprocess.TimeoutExpired(cmd="engine-server", timeout=timeout)
        self._reaped = True
        return 0


@pytest.fixture()
def spawned(monkeypatch: pytest.MonkeyPatch) -> list:
    """Record every command the module would have launched."""
    commands: list = []

    def _popen(command, **kwargs):
        commands.append(list(command))
        return _PreviousServer()

    monkeypatch.setattr(
        model_engines,
        "subprocess",
        types.SimpleNamespace(
            Popen=_popen,
            run=lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
            DEVNULL=subprocess.DEVNULL,
            PIPE=subprocess.PIPE,
            STDOUT=subprocess.STDOUT,
            TimeoutExpired=subprocess.TimeoutExpired,
        ),
    )
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True)
    return commands


@pytest.fixture()
def vllm_ready(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: "/bin/vllm")
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: None)
    _patch_runtime(monkeypatch, "hf_model_dir", lambda name: tmp_path)
    _patch_runtime(monkeypatch, "hf_model_ready", lambda name, provider: True)
    _patch_runtime(monkeypatch, "download_hf_model", lambda name, provider: None)


@pytest.fixture()
def llamacpp_ready(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    (tmp_path / "model-Q4_K_M.gguf").write_bytes(b"")
    monkeypatch.setattr(
        model_engines, "shutil", types.SimpleNamespace(which=lambda name: "/bin/llama-server")
    )
    _patch_runtime(monkeypatch, "hf_model_dir", lambda name: tmp_path)
    _patch_runtime(monkeypatch, "hf_model_ready", lambda name, provider: True)
    _patch_runtime(monkeypatch, "download_hf_model", lambda name, provider: None)


def _install(monkeypatch: pytest.MonkeyPatch, key: str, process) -> None:
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {key: process})


# ── the graceful path is unchanged ───────────────────────────────────────────


def test_a_previous_vllm_that_exits_on_terminate_is_replaced(monkeypatch, spawned, vllm_ready):
    previous = _PreviousServer()
    _install(monkeypatch, "vllm", previous)

    assert model_engines.ensure_vllm_server("org/model") is None

    assert previous.terminated is True
    assert previous.killed is False
    assert previous.waits == [10]  # one reap, no escalation
    assert spawned[0][:2] == ["/bin/vllm", "serve"]
    assert model_engines.LOCAL_SERVER_PROCESSES["vllm"] is not previous


# ── the defect: killed but never reaped ──────────────────────────────────────


def test_a_vllm_that_only_dies_on_kill_is_reaped_and_replaced(monkeypatch, spawned, vllm_ready):
    """The regression. Before the fix this returned ``None`` and spawned nothing.

    ``terminate()`` is ignored, ``kill()`` lands, and the process is a zombie
    until it is waited on — the exact state whose ``poll() is None`` used to be
    misread as "a server is already up".
    """
    previous = _PreviousServer(exits_on_terminate=False)
    _install(monkeypatch, "vllm", previous)

    assert model_engines.ensure_vllm_server("org/model") is None

    assert previous.killed is True
    assert previous.waits == [10, 5]  # the second wait is the reap
    assert previous.poll() == 0
    assert spawned[0][:2] == ["/bin/vllm", "serve"]


def test_a_vllm_that_survives_the_kill_is_a_409(monkeypatch, spawned, vllm_ready):
    previous = _PreviousServer(exits_on_terminate=False, exits_on_kill=False)
    _install(monkeypatch, "vllm", previous)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_vllm_server("org/model")

    assert err.value.status_code == 409
    assert err.value.detail == "이전 vLLM 프로세스가 종료되지 않아 새 서버를 시작할 수 없습니다."
    assert previous.waits == [10, 5]
    assert spawned == []


def test_an_already_exited_vllm_slot_is_not_signalled_at_all(monkeypatch, spawned, vllm_ready):
    """A dead entry in the table is replaced without terminate/kill."""
    previous = _PreviousServer()
    previous.terminate()
    previous.wait()
    previous.terminated = False
    _install(monkeypatch, "vllm", previous)

    assert model_engines.ensure_vllm_server("org/model") is None

    assert previous.terminated is False
    assert spawned[0][:2] == ["/bin/vllm", "serve"]


# ── llama.cpp follows the same rule ──────────────────────────────────────────


def test_a_llamacpp_that_only_dies_on_kill_is_reaped_and_replaced(
    monkeypatch, spawned, llamacpp_ready,
):
    previous = _PreviousServer(exits_on_terminate=False)
    _install(monkeypatch, "llamacpp", previous)

    assert model_engines.ensure_llamacpp_server("org/model") is None

    assert previous.waits == [10, 5]
    assert spawned[0][0] == "llama-server"


def test_a_llamacpp_that_survives_the_kill_is_a_409(monkeypatch, spawned, llamacpp_ready):
    previous = _PreviousServer(exits_on_terminate=False, exits_on_kill=False)
    _install(monkeypatch, "llamacpp", previous)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_llamacpp_server("org/model")

    assert err.value.status_code == 409
    assert err.value.detail == "이전 llama.cpp 프로세스가 종료되지 않아 새 서버를 시작할 수 없습니다."
    assert spawned == []


def test_the_reaper_is_the_single_implementation_both_engines_use():
    """Both bring-up paths call one helper, so they cannot drift apart again."""
    source = (
        sys.modules["latticeai.services.model_engines"].__loader__
        .get_source("latticeai.services.model_engines")
    )
    assert source.count("_reap_local_server(running, ") == 2
    assert "running.kill()" not in source
