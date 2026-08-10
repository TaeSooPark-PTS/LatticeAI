"""wpb03: the canonical model identity when a hint does not apply.

``ModelResolution.from_request`` layers four optional hints (a provider prefix,
an engine hint, an alias table, an external resolver) on top of the raw id the
user clicked.  The suite covers each hint *landing*; these cover each hint
*missing* — an id whose colon belongs to the repo name rather than a provider,
an alias table with no row for the chosen engine, and a resolver that declines.
``update_after_load`` gets the same treatment for the two id shapes LM Studio
does not return.

The ``model_runtime`` half covers the identity/engine seams with the same
shape: an LM Studio name that slugifies to nothing, a catalog entry the user
already downloaded, an unknown provider prefix, and the two non-MLX engine
paths through ``ensure_engine_ready``.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from latticeai.core.model_resolution import ModelResolution
from latticeai.services import model_runtime

# ── ModelResolution.from_request ────────────────────────────────────────────


def test_a_colon_that_is_not_a_provider_prefix_stays_part_of_the_model_id():
    resolution = ModelResolution.from_request("hf.co:owner/model-4bit")

    assert resolution.provider == "local_mlx", "the engine default still applies"
    assert resolution.resolved_model == "hf.co:owner/model-4bit"
    assert resolution.download_id == "hf.co:owner/model-4bit"
    assert resolution.load_id == "hf.co:owner/model-4bit"


def test_an_alias_table_without_a_row_for_the_chosen_engine_changes_nothing():
    resolution = ModelResolution.from_request(
        "gemma-4-12b-it-4bit",
        "ollama",
        engine_aliases={"gemma-4-12b-it-4bit": {"local_mlx": "mlx-community/gemma"}},
    )

    assert resolution.provider == "ollama"
    assert resolution.resolved_model == "gemma-4-12b-it-4bit"
    assert resolution.load_id == "ollama:gemma-4-12b-it-4bit"


def test_a_resolver_that_declines_leaves_the_alias_result_in_place():
    asked: List[tuple] = []

    def _resolver(model_id: str, provider: str):
        asked.append((model_id, provider))
        return ""

    resolution = ModelResolution.from_request(
        "short-name",
        "ollama",
        engine_aliases={"short-name": {"ollama": "org/real:q4"}},
        alias_resolver=_resolver,
    )

    assert asked == [("org/real:q4", "ollama")]
    assert resolution.resolved_model == "org/real:q4"
    assert resolution.load_id == "ollama:org/real:q4"


# ── ModelResolution.update_after_load ───────────────────────────────────────


def test_a_bare_instance_id_only_moves_the_expected_current_pointer():
    resolution = ModelResolution.from_request("lmstudio:org/model")
    before_load = resolution.load_id

    resolution.update_after_load(actual_current="instance-42")

    assert resolution.expected_current == "instance-42"
    assert resolution.load_id == before_load == "lmstudio:org/model"
    assert resolution.resolved_model == "org/model"


def test_a_user_scoped_id_without_a_provider_prefix_keeps_the_resolved_model():
    resolution = ModelResolution.from_request(
        "openai:gpt-test", user_email="owner@example.com"
    )

    resolution.update_after_load(actual_current="gpt-test::owner@example.com")

    assert resolution.expected_current == "gpt-test::owner@example.com"
    assert resolution.load_id == "gpt-test", "the user suffix is stripped"
    assert resolution.resolved_model == "gpt-test", "no provider half to re-read"


# ── model_runtime: LM Studio candidate keys ─────────────────────────────────


def test_a_name_that_slugifies_to_nothing_yields_only_the_raw_candidates():
    assert model_runtime._lmstudio_candidate_keys("publisher/") == ["publisher/", ""]


# ── model_runtime.engine_status: LM Studio catalog merge ────────────────────

CATALOG: Dict[str, List[Dict[str, Any]]] = {
    "local_mlx": [],
    "ollama": [],
    "vllm": [],
    "lmstudio": [
        {"id": "lmstudio:org/wpb03-catalog", "name": "WPB03 Catalog", "family": "WPB03",
         "tag": "lmstudio", "pullable": True},
    ],
    "llamacpp": [],
}


class _Router:
    current_model_id = ""

    def detected_cloud_models(self):
        return []


def test_a_downloaded_lmstudio_model_is_not_listed_again_from_the_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(model_runtime, "ENGINE_MODEL_CATALOG", CATALOG)
    monkeypatch.setattr(model_runtime, "HF_MODELS_ROOT", tmp_path / "hf-models")
    monkeypatch.setattr(model_runtime, "engine_installed", lambda engine: engine == "lmstudio")
    monkeypatch.setattr(model_runtime, "get_ollama_pulled_models", lambda: set())
    monkeypatch.setattr(model_runtime, "hf_model_ready", lambda _repo, _provider: False)
    monkeypatch.setattr(
        model_runtime,
        "get_lmstudio_models",
        lambda: [{"key": "org/wpb03-catalog", "display_name": "WPB03 Catalog",
                  "loaded_instances": []}],
    )
    monkeypatch.setattr(
        model_runtime, "engine_support_status", lambda _engine: {"supported": True, "reason": None}
    )
    monkeypatch.setattr(
        model_runtime,
        "_safe_engine_install_plan",
        lambda engine, *, base_dir: {"name": "engine:" + engine},
    )

    engines = {
        engine["id"]: engine
        for engine in model_runtime.engine_status(
            state=model_runtime.ModelRuntimeState(router=_Router(), BASE_DIR=tmp_path)
        )
    }

    models = engines["lmstudio"]["models"]
    assert [m["id"] for m in models] == ["lmstudio:org/wpb03-catalog"]
    assert models[0]["pulled"] is True, "the downloaded copy wins over the catalog row"
    assert models[0]["tag"] == "downloaded"


# ── model_runtime._resolve_model_alias ──────────────────────────────────────


def test_an_unknown_provider_prefix_is_treated_as_part_of_the_model_name(monkeypatch):
    monkeypatch.setattr(
        model_runtime, "MODEL_ENGINE_ALIASES", {"hf.co:org/model": {"local_mlx": "org/mapped"}}
    )

    assert model_runtime._resolve_model_alias("hf.co:org/model") == "org/mapped"


# ── model_runtime.ensure_engine_ready ───────────────────────────────────────


def test_an_installed_non_mlx_engine_is_reported_without_warming_mlx(monkeypatch):
    monkeypatch.setattr(model_runtime, "engine_installed", lambda _engine: True)
    monkeypatch.setattr(
        model_runtime,
        "ensure_mlx_runtime",
        lambda: pytest.fail("MLX must not be warmed for an Ollama install"),
    )

    result = model_runtime.ensure_engine_ready(
        "ollama", state=model_runtime.ModelRuntimeState()
    )

    assert result == {"engine": "ollama", "installed": True, "installed_now": False}


def test_a_freshly_installed_non_mlx_engine_reports_the_install_without_warming_mlx(monkeypatch):
    installed = {"value": False}
    monkeypatch.setattr(model_runtime, "engine_installed", lambda _engine: installed["value"])
    monkeypatch.setattr(
        model_runtime,
        "ensure_mlx_runtime",
        lambda: pytest.fail("MLX must not be warmed for an Ollama install"),
    )

    def _install(_engine, **_kwargs):
        installed["value"] = True
        return {"returncode": 0, "stdout": "ollama installed"}

    monkeypatch.setattr(model_runtime, "install_engine", _install)

    result = model_runtime.ensure_engine_ready(
        "ollama", state=model_runtime.ModelRuntimeState()
    )

    assert result["engine"] == "ollama"
    assert result["installed_now"] is True
    assert result["install"] == {"returncode": 0, "stdout": "ollama installed"}
