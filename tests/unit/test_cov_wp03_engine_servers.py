"""model_engines server bring-up — the four "make the engine answer" paths.

These functions spawn daemons, poll them and give up on a wall clock. Tests
replace three seams so none of that happens for real: the module's
``subprocess`` (fake ``Popen``/``run``), its ``time`` (a clock that only moves
when the code under test sleeps), and its HTTP probe. The assertions are on the
observable outcome — which command was spawned, which process was terminated,
which status code the caller sees — not on "it did not raise".
"""

from __future__ import annotations

import subprocess
import sys
import types
import urllib.error
import urllib.request

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


class _Clock:
    """Deterministic stand-in for ``time``: only ``sleep`` advances it."""

    def __init__(self, *, start: float = 1_000.0, step: float = 20.0) -> None:
        self.now = start
        self.step = step
        self.slept: list = []

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds) -> None:
        self.slept.append(seconds)
        self.now += self.step


class _FakeProcess:
    """A spawned server that never existed."""

    def __init__(self, *, poll_result=None, wait_raises: bool = False) -> None:
        self._poll_result = poll_result
        self._wait_raises = wait_raises
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._poll_result

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None):
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd="server", timeout=timeout)
        return 0

    def kill(self) -> None:
        self.killed = True


def _install_clock(monkeypatch, **kwargs) -> _Clock:
    clock = _Clock(**kwargs)
    monkeypatch.setattr(model_engines, "time", clock)
    return clock


def _install_subprocess(monkeypatch, *, run=None, popen=None) -> dict:
    """Give the module a subprocess module that launches nothing."""
    calls: dict = {"run": [], "popen": []}

    def _run(command, **kwargs):
        calls["run"].append((list(command), kwargs))
        if run is None:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return run(list(command), **kwargs)

    def _popen(command, **kwargs):
        calls["popen"].append((list(command), kwargs))
        if popen is None:
            return _FakeProcess()
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


def _install_probe(monkeypatch, results) -> list:
    """``_json_request`` replaced by a scripted sequence of results/raises."""
    seen: list = []
    queue = list(results)

    def _probe(url, **kwargs):
        seen.append(url)
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(model_engines, "_json_request", _probe)
    return seen


# ── LM Studio ────────────────────────────────────────────────────────────────


def test_lmstudio_server_start_is_skipped_when_it_already_answers(monkeypatch):
    seen = _install_probe(monkeypatch, [{"models": []}])
    calls = _install_subprocess(monkeypatch)

    assert model_engines.ensure_lmstudio_server() is None
    assert len(seen) == 1
    assert calls["popen"] == []


def test_lmstudio_without_a_cli_is_a_400_naming_the_missing_app(monkeypatch):
    _install_probe(monkeypatch, [OSError("refused")])
    monkeypatch.setattr(model_engines, "find_lmstudio_cli", lambda: None)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_lmstudio_server()

    assert err.value.status_code == 400
    assert "LM Studio" in str(err.value.detail)


def test_lmstudio_spawn_failure_surfaces_as_a_500(monkeypatch):
    _install_probe(monkeypatch, [OSError("refused")])
    monkeypatch.setattr(model_engines, "find_lmstudio_cli", lambda: "/bin/lms")

    def _boom(command, **kwargs):
        raise OSError("exec format error")

    _install_subprocess(monkeypatch, popen=_boom)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_lmstudio_server()

    assert err.value.status_code == 500
    assert "exec format error" in str(err.value.detail)


def test_lmstudio_is_started_then_polled_until_it_answers(monkeypatch):
    _install_probe(monkeypatch, [OSError("refused"), OSError("refused"), {"models": []}])
    monkeypatch.setattr(model_engines, "find_lmstudio_cli", lambda: "/bin/lms")
    calls = _install_subprocess(monkeypatch)
    clock = _install_clock(monkeypatch)

    assert model_engines.ensure_lmstudio_server() is None
    assert calls["popen"][0][0] == ["/bin/lms", "server", "start"]
    assert calls["popen"][0][1]["start_new_session"] is True
    assert clock.slept == [1]


def test_lmstudio_that_never_answers_times_out_as_a_500(monkeypatch):
    _install_probe(monkeypatch, [OSError("refused")])
    monkeypatch.setattr(model_engines, "find_lmstudio_cli", lambda: "/bin/lms")
    _install_subprocess(monkeypatch)
    clock = _install_clock(monkeypatch)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_lmstudio_server()

    assert err.value.status_code == 500
    assert clock.slept  # it polled before giving up


# ── Ollama ───────────────────────────────────────────────────────────────────


def test_ollama_server_needs_the_binary_first(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: None)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_ollama_server()

    assert err.value.status_code == 400


def test_ollama_already_serving_is_left_alone(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    calls = _install_subprocess(monkeypatch)

    assert model_engines.ensure_ollama_server() is None
    assert calls["run"][0][0] == ["/bin/ollama", "list"]
    assert calls["popen"] == []


def test_ollama_is_spawned_when_the_first_probe_raises(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    attempts = {"n": 0}

    def _run(command, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("daemon not up")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    calls = _install_subprocess(monkeypatch, run=_run)
    _install_clock(monkeypatch)

    assert model_engines.ensure_ollama_server() is None
    assert calls["popen"][0][0] == ["/bin/ollama", "serve"]


def test_ollama_that_stays_down_times_out_as_a_500(monkeypatch):
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    attempts = {"n": 0}

    def _run(command, **kwargs):
        attempts["n"] += 1
        if attempts["n"] % 2 == 0:
            raise OSError("still down")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    _install_subprocess(monkeypatch, run=_run)
    clock = _install_clock(monkeypatch, step=8.0)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_ollama_server()

    assert err.value.status_code == 500
    assert clock.slept == [0.5, 0.5, 0.5]


# ── OpenAI-compatible model listings ─────────────────────────────────────────


def test_lmstudio_listing_prefers_loaded_instance_ids_over_the_catalog_key(monkeypatch):
    _patch_runtime(
        monkeypatch,
        "get_lmstudio_models",
        lambda: [
            {"key": "qwen", "loaded_instances": [{"id": "qwen:2"}, {"id": ""}, "junk"]},
            {"key": "gemma", "loaded_instances": [{"no_id": True}]},
            {"key": "not-loaded"},
            {"key": "", "loaded_instances": [{"id": ""}]},
        ],
    )

    assert model_engines.get_openai_compatible_server_models("lmstudio") == ["qwen:2", "gemma"]


def test_unknown_provider_without_a_base_url_lists_nothing(monkeypatch):
    _patch_runtime(monkeypatch, "OPENAI_COMPATIBLE_PROVIDERS", {})

    assert model_engines.get_openai_compatible_server_models("nope") == []


def test_provider_models_are_read_from_the_configured_base_url(monkeypatch):
    _patch_runtime(
        monkeypatch,
        "OPENAI_COMPATIBLE_PROVIDERS",
        {
            "vllm": {
                "base_url_env": "FAKE_VLLM_BASE",
                "base_url": "http://unused/v1",
                "env_key": "FAKE_VLLM_KEY",
                "api_key_fallback": "fallback-key",
            }
        },
    )
    monkeypatch.setenv("FAKE_VLLM_BASE", "http://127.0.0.1:8000/v1/")
    monkeypatch.delenv("FAKE_VLLM_KEY", raising=False)
    seen = {}

    class _Res:
        def read(self):
            return b'{"data": [{"id": "m-1"}, {"id": "m-2"}, "junk", {"no_id": 1}]}'

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def _urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return _Res()

    monkeypatch.setattr(
        model_engines,
        "urllib",
        types.SimpleNamespace(
            request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=_urlopen),
            error=urllib.error,
        ),
    )

    assert model_engines.get_openai_compatible_server_models("vllm") == ["m-1", "m-2"]
    assert seen["url"] == "http://127.0.0.1:8000/v1/models"
    assert seen["auth"] == "Bearer fallback-key"


def test_an_unreachable_provider_lists_nothing_instead_of_raising(monkeypatch):
    _patch_runtime(
        monkeypatch,
        "OPENAI_COMPATIBLE_PROVIDERS",
        {"vllm": {"base_url": "http://127.0.0.1:8000/v1"}},
    )

    def _urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        model_engines,
        "urllib",
        types.SimpleNamespace(
            request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=_urlopen),
            error=urllib.error,
        ),
    )

    assert model_engines.get_openai_compatible_server_models("vllm") == []


def test_waiting_for_a_server_returns_once_the_model_is_listed(monkeypatch):
    listings = [[], ["target"]]
    monkeypatch.setattr(
        model_engines,
        "get_openai_compatible_server_models",
        lambda provider: listings.pop(0) if len(listings) > 1 else listings[0],
    )
    clock = _install_clock(monkeypatch)

    assert model_engines.wait_for_openai_compatible_server("vllm", "target", timeout=45) is True
    assert clock.slept == [1]


def test_waiting_for_a_server_gives_up_at_the_timeout(monkeypatch):
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    clock = _install_clock(monkeypatch, step=30.0)

    assert model_engines.wait_for_openai_compatible_server("vllm", "target", timeout=45) is False
    assert clock.slept == [1, 1]


# ── vLLM ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def hf_stubs(monkeypatch, tmp_path):
    """The three model_runtime download seams every server path reaches for."""
    state = {"downloaded": [], "ready": True, "dir": tmp_path / "model"}
    state["dir"].mkdir()
    _patch_runtime(monkeypatch, "hf_model_dir", lambda name: state["dir"])
    _patch_runtime(monkeypatch, "hf_model_ready", lambda name, provider: state["ready"])
    _patch_runtime(
        monkeypatch,
        "download_hf_model",
        lambda name, provider: state["downloaded"].append((name, provider)),
    )
    return state


def test_vllm_start_is_skipped_when_the_model_is_already_served(monkeypatch, hf_stubs):
    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda provider: ["m"]
    )
    calls = _install_subprocess(monkeypatch)

    assert model_engines.ensure_vllm_server("m") is None
    assert calls["popen"] == []


def test_vllm_without_any_runtime_is_a_400(monkeypatch, hf_stubs):
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: None)
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: None)
    monkeypatch.setattr(
        model_engines,
        "importlib",
        types.SimpleNamespace(util=types.SimpleNamespace(find_spec=lambda name: None)),
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_vllm_server("m")

    assert err.value.status_code == 400


def test_vllm_metal_serves_the_model_name_without_downloading_weights(monkeypatch, hf_stubs):
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: None)
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: "/metal/bin/python")
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {})
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True
    )
    calls = _install_subprocess(monkeypatch)

    assert model_engines.ensure_vllm_server("org/model") is None
    assert calls["popen"][0][0] == [
        "/metal/bin/python",
        "-m",
        "vllm_metal.server",
        "--model",
        "org/model",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    assert hf_stubs["downloaded"] == []
    assert model_engines.LOCAL_SERVER_PROCESSES["vllm"] is not None


def test_vllm_binary_downloads_the_model_and_reports_a_load_failure(monkeypatch, hf_stubs):
    hf_stubs["ready"] = False
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: "/bin/vllm")
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: None)
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {})
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: False
    )
    calls = _install_subprocess(monkeypatch)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_vllm_server("org/model")

    assert err.value.status_code == 500
    assert hf_stubs["downloaded"] == [("org/model", "vllm")]
    assert calls["popen"][0][0][:2] == ["/bin/vllm", "serve"]
    assert "--served-model-name" in calls["popen"][0][0]


def test_vllm_module_fallback_runs_the_openai_api_server(monkeypatch, hf_stubs):
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: None)
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: None)
    monkeypatch.setattr(
        model_engines,
        "importlib",
        types.SimpleNamespace(util=types.SimpleNamespace(find_spec=lambda name: object())),
    )
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {})
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True
    )
    calls = _install_subprocess(monkeypatch)

    assert model_engines.ensure_vllm_server("org/model") is None
    assert calls["popen"][0][0][:3] == [
        sys.executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
    ]


def test_an_unkillable_vllm_process_is_a_409_rather_than_a_silent_success(monkeypatch, hf_stubs):
    """v11.0.1 D5 — a process that survives terminate *and* kill is reported.

    The old code killed without reaping, so ``poll()`` kept returning ``None``
    for the zombie, a recheck read that as "already running", and the caller
    got ``None`` back with no server started. The kill is followed by a second
    ``wait`` now, and a process that is still there afterwards is a 409.
    """
    stuck = _FakeProcess(poll_result=None, wait_raises=True)
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: "/bin/vllm")
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: None)
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {"vllm": stuck})
    calls = _install_subprocess(monkeypatch)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_vllm_server("org/model")

    assert err.value.status_code == 409
    assert "이전 vLLM 프로세스" in err.value.detail
    assert stuck.terminated is True
    assert stuck.killed is True
    assert calls["popen"] == []  # the old process still holds the port


def test_a_foreign_vllm_server_is_a_409_rather_than_a_takeover(monkeypatch, hf_stubs):
    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda provider: ["other"]
    )
    monkeypatch.setattr(model_engines, "vllm_executable", lambda: "/bin/vllm")
    monkeypatch.setattr(model_engines, "vllm_metal_python", lambda: None)
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {})
    _install_subprocess(monkeypatch)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_vllm_server("org/model")

    assert err.value.status_code == 409


# ── llama.cpp ────────────────────────────────────────────────────────────────


def test_llamacpp_start_is_skipped_when_the_model_is_already_served(monkeypatch, hf_stubs):
    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda provider: ["m"]
    )
    calls = _install_subprocess(monkeypatch)

    assert model_engines.ensure_llamacpp_server("m") is None
    assert calls["popen"] == []


def test_a_foreign_llamacpp_server_is_a_409(monkeypatch, hf_stubs):
    monkeypatch.setattr(
        model_engines, "get_openai_compatible_server_models", lambda provider: ["other"]
    )
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {})

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_llamacpp_server("m")

    assert err.value.status_code == 409


def test_llamacpp_without_the_server_binary_is_a_400(monkeypatch, hf_stubs):
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {})
    monkeypatch.setattr(
        model_engines, "shutil", types.SimpleNamespace(which=lambda name: None)
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_llamacpp_server("m")

    assert err.value.status_code == 400


def test_llamacpp_reports_a_download_that_produced_no_gguf(monkeypatch, hf_stubs):
    hf_stubs["ready"] = False
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {})
    monkeypatch.setattr(
        model_engines, "shutil", types.SimpleNamespace(which=lambda name: "/bin/llama-server")
    )

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_llamacpp_server("org/model")

    assert err.value.status_code == 500
    assert hf_stubs["downloaded"] == [("org/model", "llamacpp")]


def test_llamacpp_prefers_the_q4_k_m_quantisation(monkeypatch, hf_stubs):
    (hf_stubs["dir"] / "model-f16.gguf").write_bytes(b"")
    (hf_stubs["dir"] / "model-Q4_K_M.gguf").write_bytes(b"")
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {})
    monkeypatch.setattr(
        model_engines, "shutil", types.SimpleNamespace(which=lambda name: "/bin/llama-server")
    )
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True
    )
    calls = _install_subprocess(monkeypatch)

    assert model_engines.ensure_llamacpp_server("org/model") is None
    command = calls["popen"][0][0]
    assert command[0] == "llama-server"
    assert command[2].endswith("model-Q4_K_M.gguf")
    assert command[3:] == ["--alias", "org/model", "--host", "127.0.0.1", "--port", "8080"]


def test_llamacpp_falls_back_to_the_first_gguf_and_reports_a_load_failure(monkeypatch, hf_stubs):
    (hf_stubs["dir"] / "a-f16.gguf").write_bytes(b"")
    (hf_stubs["dir"] / "b-f32.gguf").write_bytes(b"")
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {})
    monkeypatch.setattr(
        model_engines, "shutil", types.SimpleNamespace(which=lambda name: "/bin/llama-server")
    )
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: False
    )
    calls = _install_subprocess(monkeypatch)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_llamacpp_server("org/model")

    assert err.value.status_code == 500
    assert calls["popen"][0][0][2].endswith("a-f16.gguf")


def test_an_unkillable_llamacpp_process_is_a_409_like_vllm(monkeypatch, hf_stubs):
    """v11.0.1 D5 — llama.cpp reaps and reports on the same rule as vLLM.

    This path used to start a second ``llama-server`` on top of a process that
    had survived both signals and still held port 8080.
    """
    stuck = _FakeProcess(poll_result=None, wait_raises=True)
    (hf_stubs["dir"] / "a-f16.gguf").write_bytes(b"")
    monkeypatch.setattr(model_engines, "get_openai_compatible_server_models", lambda provider: [])
    monkeypatch.setattr(model_engines, "LOCAL_SERVER_PROCESSES", {"llamacpp": stuck})
    monkeypatch.setattr(
        model_engines, "shutil", types.SimpleNamespace(which=lambda name: "/bin/llama-server")
    )
    monkeypatch.setattr(
        model_engines, "wait_for_openai_compatible_server", lambda *a, **k: True
    )
    calls = _install_subprocess(monkeypatch)

    with pytest.raises(ModelRuntimeError) as err:
        model_engines.ensure_llamacpp_server("org/model")

    assert err.value.status_code == 409
    assert "이전 llama.cpp 프로세스" in err.value.detail
    assert stuck.terminated is True
    assert stuck.killed is True
    assert calls["popen"] == []
