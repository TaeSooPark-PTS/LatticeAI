"""Optional-backend import contract for ``latticeai/models/router/loading.py``.

The router imports ``openai``, ``mlx.core``, ``mlx_vlm`` and ``mlx_lm`` behind
``try/except`` at module scope, and ``ensure_mlx_runtime()`` retries the same
three MLX imports after an installer has run. Which side of each ``except``
executes depends entirely on what happens to be installed on the machine
running the suite: on an Apple Silicon dev box every import succeeds, on the
ubuntu CI leg every one of them fails. Neither side proves the other works.

These tests pin both directions everywhere by executing that module again as a
private module object with ``sys.modules`` prepared — present (fakes) and
absent (``None`` entries, which make ``import`` raise). ``importlib.reload``
is deliberately not used: it would rebind the live module's globals for the
rest of the session.

The v11.3.0 decomposition moved the guarded imports (and every method that
reads them) into ``latticeai.models.router.loading``, so that is what is
re-executed and what a stand-in patches — a name rebound on the package
``__init__`` would leave the reads in ``loading`` untouched, and the package
deliberately does not re-export the five rebindable MLX names at all.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

import pytest

from latticeai.models import router as router_mod
from latticeai.models.router import loading as loading_mod

ROUTER_PATH = loading_mod.__file__
OPTIONAL_MODULES = ("openai", "mlx", "mlx.core", "mlx_vlm", "mlx_lm")


class _FakeAsyncOpenAI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _fake_mlx_modules() -> dict[str, types.ModuleType]:
    core = types.ModuleType("mlx.core")
    core.gpu = object()
    core.set_default_device = lambda _device: None
    mlx = types.ModuleType("mlx")
    mlx.core = core
    vlm = types.ModuleType("mlx_vlm")
    vlm.load = lambda _model_id: (object(), object())
    lm = types.ModuleType("mlx_lm")
    lm.load = lambda _model_id: (object(), object())
    return {"mlx": mlx, "mlx.core": core, "mlx_vlm": vlm, "mlx_lm": lm}


def _absent(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Make ``import <name>`` raise, the way it does where the wheel is absent.

    A ``None`` entry in ``sys.modules`` is the documented way to poison an
    import; the interpreter raises ``ImportError`` before touching the finders.
    """
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)


def _reimport(monkeypatch: pytest.MonkeyPatch, name: str) -> types.ModuleType:
    # Executed under a dotted name inside the real package so the module's own
    # relative imports (``from .catalog import …``) resolve against the already
    # loaded siblings — the module under test is a submodule now, not a
    # top-level file.
    qualified = f"{router_mod.__name__}.{name}"
    spec = importlib.util.spec_from_file_location(qualified, ROUTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, qualified, module)
    spec.loader.exec_module(module)
    return module


def test_router_imports_cleanly_with_no_optional_backend_installed(monkeypatch):
    monkeypatch.setenv("MLX_VLM_DRAFT_KIND", "mtp")
    _absent(monkeypatch, *OPTIONAL_MODULES)

    module = _reimport(monkeypatch, "_wp09_router_without_backends")
    try:
        assert module.AsyncOpenAI is None
        assert module.mx is None
        assert module.vlm_load is None
        assert module.lm_load is None
        assert module.VLM_AVAILABLE is False
        assert module.LM_AVAILABLE is False
        # The module still imports, so every pure helper stays usable — that
        # is the whole point of guarding the optional imports. They live in
        # backend-free sibling submodules, which this import just pulled in.
        assert router_mod.normalize_branding("커넥트 AI 입니다") == "Lattice AI 입니다"
        assert router_mod.parse_model_ref("openai:gpt-4o") == ("openai", "gpt-4o")
    finally:
        module.executor.shutdown(wait=False)


def test_router_binds_every_optional_backend_when_all_are_installed(monkeypatch):
    monkeypatch.setenv("MLX_VLM_DRAFT_KIND", "mtp")
    fakes = _fake_mlx_modules()
    openai_module = types.ModuleType("openai")
    openai_module.AsyncOpenAI = _FakeAsyncOpenAI
    for name, fake in {**fakes, "openai": openai_module}.items():
        monkeypatch.setitem(sys.modules, name, fake)

    module = _reimport(monkeypatch, "_wp09_router_with_backends")
    try:
        assert module.AsyncOpenAI is _FakeAsyncOpenAI
        assert module.mx is fakes["mlx.core"]
        assert module.vlm_load is fakes["mlx_vlm"].load
        assert module.lm_load is fakes["mlx_lm"].load
        assert module.VLM_AVAILABLE is True
        assert module.LM_AVAILABLE is True
        # Everything already bound: the runtime check is a no-op, not a retry.
        assert module.ensure_mlx_runtime() is None
    finally:
        module.executor.shutdown(wait=False)


def test_router_import_defaults_the_draft_kind_without_overriding_an_operator(monkeypatch):
    monkeypatch.setenv("MLX_VLM_DRAFT_KIND", "operator-choice")
    _absent(monkeypatch, *OPTIONAL_MODULES)

    module = _reimport(monkeypatch, "_wp09_router_draft_kind")
    try:
        assert os.environ["MLX_VLM_DRAFT_KIND"] == "operator-choice"
        assert module.VLM_AVAILABLE is False
    finally:
        module.executor.shutdown(wait=False)


def _clear_mlx_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the live module in the state a fresh non-MLX import leaves behind.

    ``loading`` is where the five names are defined and where every read of
    them happens, so it is both what is cleared here and what
    ``ensure_mlx_runtime`` rebinds.
    """
    monkeypatch.setattr(loading_mod, "mx", None)
    monkeypatch.setattr(loading_mod, "vlm_load", None)
    monkeypatch.setattr(loading_mod, "lm_load", None)
    monkeypatch.setattr(loading_mod, "VLM_AVAILABLE", False)
    monkeypatch.setattr(loading_mod, "LM_AVAILABLE", False)


def test_ensure_mlx_runtime_rebinds_the_globals_after_an_install(monkeypatch):
    _clear_mlx_globals(monkeypatch)
    fakes = _fake_mlx_modules()
    devices = []
    fakes["mlx.core"].set_default_device = devices.append
    for name, fake in fakes.items():
        monkeypatch.setitem(sys.modules, name, fake)

    loading_mod.ensure_mlx_runtime()

    assert loading_mod.mx is fakes["mlx.core"]
    assert loading_mod.vlm_load is fakes["mlx_vlm"].load
    assert loading_mod.lm_load is fakes["mlx_lm"].load
    assert loading_mod.VLM_AVAILABLE is True
    assert loading_mod.LM_AVAILABLE is True
    # The GPU is selected as part of binding, not later at generation time.
    assert devices == [fakes["mlx.core"].gpu]


def test_ensure_mlx_runtime_accepts_a_text_only_install(monkeypatch):
    """mlx-lm alone is a supported runtime: the VLM path is optional."""
    _clear_mlx_globals(monkeypatch)
    fakes = _fake_mlx_modules()
    for name in ("mlx", "mlx.core", "mlx_lm"):
        monkeypatch.setitem(sys.modules, name, fakes[name])
    _absent(monkeypatch, "mlx_vlm")

    loading_mod.ensure_mlx_runtime()

    assert loading_mod.mx is fakes["mlx.core"]
    assert loading_mod.vlm_load is None
    assert loading_mod.VLM_AVAILABLE is False
    assert loading_mod.lm_load is fakes["mlx_lm"].load
    assert loading_mod.LM_AVAILABLE is True


def test_ensure_mlx_runtime_names_every_backend_it_could_not_import(monkeypatch):
    _clear_mlx_globals(monkeypatch)
    _absent(monkeypatch, "mlx", "mlx.core", "mlx_vlm", "mlx_lm")

    with pytest.raises(RuntimeError) as excinfo:
        loading_mod.ensure_mlx_runtime()

    message = str(excinfo.value)
    assert "MLX runtime is not available after install" in message
    # All three failures are reported together, so an operator fixing one
    # package does not have to re-run to discover the next.
    assert "mlx:" in message
    assert "mlx-vlm:" in message
    assert "mlx-lm:" in message
    assert loading_mod.mx is None
    assert loading_mod.VLM_AVAILABLE is False
    assert loading_mod.LM_AVAILABLE is False


def test_ensure_mlx_runtime_fails_when_only_the_core_package_is_importable(monkeypatch):
    """Core without a loader cannot generate, so it is still a hard failure."""
    _clear_mlx_globals(monkeypatch)
    fakes = _fake_mlx_modules()
    monkeypatch.setitem(sys.modules, "mlx", fakes["mlx"])
    monkeypatch.setitem(sys.modules, "mlx.core", fakes["mlx.core"])
    _absent(monkeypatch, "mlx_vlm", "mlx_lm")

    with pytest.raises(RuntimeError, match="MLX runtime is not available after install"):
        loading_mod.ensure_mlx_runtime()

    assert loading_mod.mx is fakes["mlx.core"]
    assert loading_mod.vlm_load is None
    assert loading_mod.lm_load is None


def test_mlx_sampler_defers_to_the_bundled_backend_sampler():
    assert loading_mod._mlx_sampler(0.7) is None
