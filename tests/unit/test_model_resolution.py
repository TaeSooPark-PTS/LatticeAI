"""Unit tests for latticeai.core.model_resolution (피드백 #1 / #2)."""

import pytest

from latticeai.core.model_resolution import (
    ModelResolution,
    PrepareReport,
    PrepareState,
)
from latticeai.services.model_catalog import MODEL_ENGINE_ALIASES

ALIASES = {
    "gemma-4-12b-it-4bit": {
        "local_mlx": "mlx-community/gemma-4-12b-it-4bit",
        "ollama": "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
        "lmstudio": "ggml-org/gemma-4-12B-it-GGUF",
        "llamacpp": "ggml-org/gemma-4-12B-it-GGUF",
    },
}


def test_resolution_for_mlx():
    r = ModelResolution.from_request(
        "gemma-4-12b-it-4bit", engine="local_mlx", engine_aliases=ALIASES, user_email="t@x"
    )
    assert r.engine == "local_mlx"
    assert r.resolved_model == "mlx-community/gemma-4-12b-it-4bit"
    assert r.download_id == "mlx-community/gemma-4-12b-it-4bit"
    assert r.load_id == "mlx-community/gemma-4-12b-it-4bit"
    # local_mlx → user suffix 없이
    assert r.expected_current == "mlx-community/gemma-4-12b-it-4bit"


def test_resolution_for_ollama_appends_user_in_expected_current():
    r = ModelResolution.from_request(
        "gemma-4-12b-it-4bit", engine="ollama", engine_aliases=ALIASES, user_email="taesoo@example.com"
    )
    assert r.engine == "ollama"
    assert r.load_id == "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"
    assert r.expected_current == "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M::taesoo@example.com"


def test_gemma4_catalog_alias_can_route_to_gguf_runtime():
    r = ModelResolution.from_request(
        "gemma-4-12b-it-4bit", engine="lmstudio", engine_aliases=ALIASES, user_email="t@x"
    )
    assert r.engine == "lmstudio"
    assert r.resolved_model == "ggml-org/gemma-4-12B-it-GGUF"
    assert r.load_id == "lmstudio:ggml-org/gemma-4-12B-it-GGUF"
    assert r.load_id != "mlx-community/gemma-4-12b-it-4bit"


def test_gemma4_catalog_aliases_do_not_force_mlx_vlm():
    # Lookups fold case; the value is the Hub's canonical id (capital B).
    aliases = MODEL_ENGINE_ALIASES["mlx-community/gemma-4-12b-it-4bit"]
    assert aliases["local_mlx"] == "mlx-community/gemma-4-12B-it-4bit"
    assert aliases["ollama"] == "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"
    assert aliases["lmstudio"] == "ggml-org/gemma-4-12B-it-GGUF"
    assert aliases["llamacpp"] == "ggml-org/gemma-4-12B-it-GGUF"

    ollama = ModelResolution.from_request(
        "mlx-community/gemma-4-12b-it-4bit",
        engine="ollama",
        engine_aliases=MODEL_ENGINE_ALIASES,
    )
    assert ollama.engine == "ollama"
    assert ollama.load_id == "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"


def test_gemma4_26b_common_name_resolves_to_a4b_mlx_repo():
    r = ModelResolution.from_request(
        "gemma-4-26b-it-4bit",
        engine="local_mlx",
        engine_aliases=MODEL_ENGINE_ALIASES,
    )
    assert r.engine == "local_mlx"
    assert r.resolved_model == "mlx-community/gemma-4-26b-a4b-it-4bit"
    assert r.load_id == "mlx-community/gemma-4-26b-a4b-it-4bit"


def test_resolution_for_explicit_prefix():
    r = ModelResolution.from_request("ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M", user_email="t@x")
    assert r.engine == "ollama"
    assert r.resolved_model == "hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"
    assert r.load_id == "ollama:hf.co/ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"


def test_resolution_update_after_load_syncs_lmstudio_instance():
    r = ModelResolution.from_request("lmstudio:ggml-org/gemma-4-12B-it-GGUF", user_email="t@x")
    assert r.engine == "lmstudio"
    r.update_after_load(actual_current="lmstudio:instance-abc::t@x")
    assert r.load_id == "lmstudio:instance-abc"
    assert r.expected_current == "lmstudio:instance-abc::t@x"
    assert r.resolved_model == "instance-abc"


def test_resolution_empty_raises():
    with pytest.raises(ValueError):
        ModelResolution.from_request("")


def test_prepare_state_enum_complete():
    for member in [
        "RESOLVING", "ENGINE_CHECK", "ENGINE_INSTALL", "DOWNLOADING",
        "SERVER_STARTING", "MODEL_LOADING", "SMOKE_TEST",
        "READY", "DEGRADED", "FAILED",
    ]:
        assert getattr(PrepareState, member).value == member


def test_prepare_report_roundtrip():
    r = ModelResolution.from_request("gemma-4-12b-it-4bit", engine="local_mlx", engine_aliases=ALIASES)
    report = PrepareReport(
        status="ok",
        state=PrepareState.READY,
        resolution=r,
        current=r.expected_current,
        downloaded=True,
        loaded=True,
        ready_to_chat=True,
        compatibility_status="ok",
    )
    d = report.to_dict()
    assert d["state"] == "READY"
    assert d["resolution"]["load_id"] == r.load_id
    assert d["ready_to_chat"] is True
