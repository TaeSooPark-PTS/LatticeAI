"""wpb05 — model loading and the workspace tools: the untaken directions.

The MLX loader, the Ollama pull reader and the model-preparation SSE loop are
all driven through their module seams (``monkeypatch.setattr`` on the module's
own names), never against a real GPU, a real ``ollama`` binary or a real
subprocess — so the arcs behave the same on the ubuntu coverage leg as they do
on Apple Silicon. The two tool tests run entirely inside ``tmp_path`` with
``AGENT_ROOT`` rebound to it.
"""

from __future__ import annotations

import asyncio
import queue as stdlib_queue
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from latticeai.core.model_resolution import ModelResolution
from latticeai.models import router as router_mod

# The optional-backend bindings (``mx`` / ``vlm_load`` / ``lm_load`` /
# ``VLM_AVAILABLE`` / ``LM_AVAILABLE`` / ``AsyncOpenAI``) and the two callables
# the load path reaches through them are read in the submodule that defines
# them, so after the v11.3.0 split the stand-ins land on ``.loading`` — a name
# rebound on the package ``__init__`` would leave those reads untouched.
from latticeai.models.router import loading as router_loading

# ``hf_model_dir`` reads the root from its own module globals, so after the
# v11.3.0 split the temp-dir stand-in lands on ``.local_models``.
from latticeai.models.router import local_models as router_local_models
from latticeai.services import model_engines, model_loading
from latticeai.tools import commands as commands_mod
from latticeai.tools import filesystem as filesystem_mod

STREAM_TIMEOUT = 10.0


class _FakeMx:
    gpu = object()

    def __init__(self) -> None:
        self.devices: List[Any] = []

    def set_default_device(self, device: Any) -> None:
        self.devices.append(device)

    def clear_cache(self) -> None:
        return None


class _TemplateTokenizer:
    def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
        return "PROMPT"


# ── models/router ────────────────────────────────────────────────────────────


def test_touching_the_registry_with_nothing_loaded_records_no_timestamp():
    router = router_mod.LLMRouter()

    router._touch()

    assert router.model_memory_policy()["last_used"] == {}


def test_a_draft_model_is_skipped_when_the_text_loader_vanishes_mid_load(
    monkeypatch, tmp_path: Path
):
    """Defensive arm of the assistant loader.

    The Gemma 4 recovery path reaches the draft step through ``mlx_lm``. If the
    MLX runtime is re-bound while the target is loading, ``lm_load`` is gone by
    the time the assistant would load — the stack must come back without a
    draft rather than calling ``None``.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", tmp_path / "hf-models")
    monkeypatch.setattr(router_loading, "ensure_mlx_runtime", lambda: None)
    monkeypatch.setattr(router_loading, "mx", _FakeMx())
    monkeypatch.setattr(router_loading, "vlm_load", None)
    loaded: List[str] = []

    def fake_lm_load(model_id: str):
        loaded.append(model_id)
        # The runtime is torn down behind us, exactly as a concurrent
        # ``ensure_mlx_runtime`` rebind would leave it — which means the
        # binding in ``.loading``, the only one the load path reads.
        router_loading.lm_load = None
        return object(), _TemplateTokenizer()

    monkeypatch.setattr(router_loading, "lm_load", fake_lm_load)
    router = router_mod.LLMRouter()

    result = asyncio.run(
        router.load_model(
            "mlx-community/gemma-4-12b-it", draft_model_id="mlx-community/gemma-4-draft",
        )
    )

    cache_key = "mlx-community/gemma-4-12b-it_mlx-community/gemma-4-draft"
    assert result == f"Success: {cache_key} (mlx_lm)"
    assert loaded == ["mlx-community/gemma-4-12b-it"], "the assistant was never loaded"
    assert router._cache[cache_key][2] is None
    assert router._cache[cache_key][3] == "mlx_lm"


# ── services/model_engines ───────────────────────────────────────────────────


class _PullProcess:
    """``ollama pull`` without ollama: a scripted stdout and an exit code."""

    def __init__(self, lines: List[str], *, returncode: int = 0) -> None:
        self.stdout = iter(lines)
        self._returncode = returncode

    def wait(self) -> int:
        return self._returncode

    def kill(self) -> None:  # pragma: no cover - only used by the timeout path
        return None


def test_a_percentage_line_is_parsed_even_with_nobody_listening(monkeypatch):
    """Progress is still tracked without a listener — the emit is what is skipped."""
    monkeypatch.setattr(model_engines, "local_binary", lambda name: "/bin/ollama")
    popen_calls: List[Any] = []

    def _popen(command, **kwargs):
        popen_calls.append(list(command))
        return _PullProcess(["pulling 1a2b:  45% ▕███  ▏\n", "success\n"])

    monkeypatch.setattr(
        model_engines,
        "subprocess",
        types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""),
            Popen=_popen,
            PIPE=-1,
            STDOUT=-2,
            TimeoutExpired=Exception,
        ),
    )

    result = model_engines.pull_ollama_model_with_progress("qwen3:8b", None)

    assert result == {"provider": "ollama", "model": "qwen3:8b", "returncode": 0}
    assert popen_calls == [["/bin/ollama", "pull", "qwen3:8b"]]


# ── services/model_loading ───────────────────────────────────────────────────


class _NoisyQueue(stdlib_queue.Queue):
    """Delivers one frame the SSE loop does not understand, then behaves."""

    def __init__(self) -> None:
        super().__init__()
        self._noise_pending = True

    def get(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if self._noise_pending:
            self._noise_pending = False
            return {"kind": "wpb05-unknown"}
        return super().get(*args, **kwargs)


def _stream_deps(**overrides: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "normalize_local_model_request": lambda model_id, engine: model_id,
        "parse_model_ref": lambda model_id: ("openai", model_id),
        "_model_runtime_compatibility": lambda _model, engine=None: {"supported": True},
        "model_download_progress_payload": lambda stage, message, **kwargs: {
            "stage": stage, "message": message, **kwargs,
        },
        "get_current_user": lambda _request: "wpb05@example.com",
        "get_user_api_key": lambda _email, _provider: None,
        "router": SimpleNamespace(
            current_model_id=None,
            load_model=None,
        ),
        "_ModelResolution": ModelResolution,
        "MODEL_ENGINE_ALIASES": {},
        "_smoke_test_loaded_model": None,
        "_friendly_model_runtime_error": lambda exc, model_id=None, engine=None: str(exc),
    }
    base.update(overrides)
    return base


def test_an_unrecognised_worker_frame_is_ignored_instead_of_ending_the_stream(monkeypatch):
    """The SSE loop keeps draining until it sees the frame it is waiting for."""
    async def _load_model(model_id, adapter_path, **kwargs):
        return f"loaded {model_id}"

    router = SimpleNamespace(current_model_id=None, load_model=_load_model)

    async def _smoke(_resolution, api_key_override=None):
        return {"ok": True, "status": "ok"}

    deps = _stream_deps(router=router, _smoke_test_loaded_model=_smoke)
    monkeypatch.setattr(model_loading, "_get_model_runtime_deps", lambda _state: deps)
    monkeypatch.setattr(
        model_loading, "queue", types.SimpleNamespace(Queue=_NoisyQueue)
    )

    async def _drain() -> List[str]:
        frames: List[str] = []
        stream = model_loading.prepare_and_load_model_stream(
            "openai:gpt-4o-mini", object(), runtime_state=SimpleNamespace(),
        )
        async for frame in stream:
            frames.append(frame)
        return frames

    frames = asyncio.run(asyncio.wait_for(_drain(), STREAM_TIMEOUT))

    assert any("event: done" in frame for frame in frames)
    assert not any("wpb05-unknown" in frame for frame in frames)


# ── tools/commands ───────────────────────────────────────────────────────────


def test_a_find_command_with_only_allowed_flags_runs(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("latticeai.tools.AGENT_ROOT", workspace)
    monkeypatch.setattr(commands_mod.tools, "AGENT_ROOT", workspace)
    monkeypatch.setattr(commands_mod.shutil, "which", lambda name, path=None: "/usr/bin/find")
    ran: List[Any] = []

    def _run(command, **kwargs):
        ran.append((list(command), kwargs))
        return SimpleNamespace(returncode=0, stdout="./a.md\n", stderr="")

    monkeypatch.setattr(
        commands_mod,
        "subprocess",
        SimpleNamespace(run=_run, TimeoutExpired=RuntimeError),
    )

    result = commands_mod.run_command("find . -name *.md")

    assert result["returncode"] == 0
    assert result["stdout"] == "./a.md\n"
    assert ran[0][0] == ["/usr/bin/find", ".", "-name", "*.md"]


def test_find_still_refuses_a_flag_that_would_execute_something(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("latticeai.tools.AGENT_ROOT", workspace)
    monkeypatch.setattr(commands_mod.tools, "AGENT_ROOT", workspace)

    with pytest.raises(commands_mod.ToolError, match="find flags are not allowed"):
        commands_mod.run_command("find . -delete")


# ── tools/filesystem ─────────────────────────────────────────────────────────


def test_search_reads_a_whole_file_before_moving_on_when_nothing_matches(
    monkeypatch, tmp_path: Path
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("latticeai.tools.AGENT_ROOT", workspace)
    monkeypatch.setattr(filesystem_mod.tools, "AGENT_ROOT", workspace)
    (workspace / "a-miss.md").write_text("첫 줄\n두 번째 줄\n", encoding="utf-8")
    (workspace / "b-hit.md").write_text("앞줄\nwpb05-needle 여기\n", encoding="utf-8")

    result = filesystem_mod.search_files("wpb05-needle", ".")

    assert result["query"] == "wpb05-needle"
    assert result["matches"] == [
        {"path": "b-hit.md", "line": 2, "preview": "wpb05-needle 여기"}
    ]
