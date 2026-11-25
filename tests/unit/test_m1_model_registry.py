from __future__ import annotations

from latticeai.core import model_compat
from latticeai.services import model_catalog
from latticeai.services.model_capability_registry import (
    LEGACY,
    RECOMMENDED,
    get_all_capabilities,
    get_capability,
    get_legacy_capabilities,
    is_recognized_model,
)


def _recommended():
    return [c for c in get_all_capabilities() if c.lifecycle == RECOMMENDED]


def test_deleted_models_are_gone_from_every_list():
    caps = get_all_capabilities()
    ids = {c.id for c in caps} | {c.hf_repo_id for c in caps}
    for gone in (
        "mlx-community/phi-3.5-vision-4bit",
        "mlx-community/moondream2-4bit",
        "google/gemma-3-4b-it",
        "google/gemma-3-12b-it",
        "meta-llama/Llama-3.2-11B-Vision-Instruct",
        "mistralai/Pixtral-12B-2409",
        "Qwen/Qwen2.5-VL-7B-Instruct",
    ):
        assert gone not in ids, gone


def test_recognized_and_recommended_are_separate_lists():
    recommended = _recommended()
    legacy = get_legacy_capabilities()

    assert all(c.lifecycle == RECOMMENDED for c in recommended)
    assert all(c.lifecycle == LEGACY for c in legacy)
    assert not {c.id for c in recommended} & {c.id for c in legacy}
    assert get_all_capabilities() == [*recommended, *legacy]

    superseded = "mlx-community/Qwen3-VL-8B-Instruct-4bit"
    assert is_recognized_model(superseded) is True
    assert get_capability(superseded).lifecycle == LEGACY

    current = "mlx-community/gemma-4-12B-it-4bit"
    assert is_recognized_model(current) is True
    assert get_capability(current).lifecycle == RECOMMENDED

    assert is_recognized_model("nobody/never-existed") is False
    assert get_capability("nobody/never-existed") is None


def test_every_entry_pins_an_architecture_and_a_measured_size():
    for cap in get_all_capabilities():
        assert cap.architecture, cap.id
        assert cap.download_size_gb and cap.download_size_gb > 0, cap.id
        shown = float(cap.size.rstrip("GB"))
        assert abs(shown - cap.download_size_gb) <= 0.2, (cap.id, cap.size)


def test_the_registry_id_is_the_hub_canonical_casing():
    cap = get_capability("mlx-community/gemma-4-12B-it-4bit")

    assert cap is not None
    assert cap.id == cap.hf_repo_id == "mlx-community/gemma-4-12B-it-4bit"
    assert (
        model_catalog.MODEL_ENGINE_ALIASES["gemma-4-12b-it-4bit"]["local_mlx"] == cap.id
    )


def test_catalog_ids_are_reachable_engine_targets():
    for engine, models in model_catalog.ENGINE_MODEL_CATALOG.items():
        if engine == "local_mlx":
            continue
        for model in models:
            model_id = str(model["id"])
            assert model_id.startswith(f"{engine}:"), model_id
            target = model_id.split(":", 1)[1]
            routes = model_catalog.MODEL_ENGINE_ALIASES[
                str(model["hf_repo_id"]).lower()
            ]
            assert target == routes[engine], (engine, model_id)


def test_no_engine_route_points_at_a_gated_repo():
    gated_owners = ("meta-llama/", "google/gemma-3")
    for routes in model_catalog.MODEL_ENGINE_ALIASES.values():
        for engine, target in routes.items():
            assert not target.startswith(gated_owners), (engine, target)


def test_an_unrouted_entry_keeps_its_own_id_rather_than_inventing_one():
    entry = model_catalog._normalize_engine_entry(
        "ollama",
        {"id": "org/no-routes", "hf_repo_id": "org/no-routes", "size": "9GB"},
    )

    assert entry["id"] == "org/no-routes"
    assert entry["size"] == "실행 도구에서 관리"


def test_a_common_misname_still_resolves():
    routes = model_catalog.MODEL_ENGINE_ALIASES["gemma-4-26b-it-4bit"]

    assert routes["local_mlx"] == "mlx-community/gemma-4-26b-a4b-it-4bit"
    assert (
        model_catalog.MODEL_ENGINE_ALIASES["gemma-4-26b-qat-4bit"]["local_mlx"]
        == "mlx-community/gemma-4-26B-A4B-it-qat-4bit"
    )
    assert (
        model_catalog.MODEL_ENGINE_ALIASES["gemma-4-26b-optiq-4bit"]["local_mlx"]
        == "mlx-community/gemma-4-26B-A4B-it-qat-OptiQ-4bit"
    )
    assert (
        model_catalog.MODEL_ENGINE_ALIASES["qwen-3.8-27b-4bit"]["local_mlx"]
        == "mlx-community/Qwen3.8-27B-4bit"
    )


def test_one_generation_may_ship_two_minor_releases():
    ids = {m["id"] for m in model_catalog.ENGINE_MODEL_CATALOG["local_mlx"]}

    assert "mlx-community/Qwen3.5-9B-MLX-4bit" in ids
    assert "mlx-community/Qwen3.6-27B-4bit" in ids

    rows = [
        {"id": "x/Qwen2.5-VL-7B", "family": "Qwen2.5-VL", "name": "Qwen2.5-VL 7B"},
        {"id": "x/Qwen3.5-9B", "family": "Qwen3.5", "name": "Qwen3.5 9B"},
        {"id": "x/Qwen3.6-27B", "family": "Qwen3.6", "name": "Qwen3.6 27B"},
    ]
    kept = [row["id"] for row in model_catalog.filter_lower_family_versions(rows)]

    assert kept == ["x/Qwen3.5-9B", "x/Qwen3.6-27B"]


def test_the_new_local_families_are_detected_from_their_ids():
    assert (
        model_compat.detect_model_family("mlx-community/gpt-oss-20b-MXFP4-Q8")
        == "gpt_oss"
    )
    assert model_compat.detect_model_family("mlx-community/LFM2.5-2.6B-4bit") == "lfm2"
    assert model_compat.detect_model_family("openai:gpt-4o-mini") == "gpt"


def test_the_new_families_have_their_own_profiles():
    gpt_oss = model_compat.get_model_profile(
        "mlx-community/gpt-oss-20b-MXFP4-Q8", "local_mlx"
    )
    lfm2 = model_compat.get_model_profile("mlx-community/LFM2.5-2.6B-4bit", "local_mlx")

    assert gpt_oss["supports_vision"] is False
    assert lfm2["supports_vision"] is False
    assert "<|return|>" in gpt_oss["stop_sequences"]
    assert "<|im_end|>" in lfm2["stop_sequences"]


def test_an_architecture_maps_to_a_family():
    for arch, family in (
        ("qwen3_5", "qwen"),
        ("qwen3_5_moe", "qwen"),
        ("gpt_oss", "gpt_oss"),
        ("lfm2", "lfm2"),
        ("gemma4_unified", "gemma"),
        ("mllama", "llama"),
    ):
        assert model_compat.family_for_architecture(arch) == family

    assert model_compat.family_for_architecture("something_new") == "unknown"
    assert model_compat.family_for_architecture(None) == "unknown"


def test_a_known_architecture_outranks_the_id_but_an_unknown_one_defers():
    assert (
        model_compat.detect_model_family("org/mystery-4bit", architecture="mllama")
        == "llama"
    )
    assert (
        model_compat.detect_model_family(
            "mlx-community/gemma-4-12B-it-4bit",
            architecture="brand_new_arch",
        )
        == "gemma"
    )
    assert (
        model_compat.detect_model_family("", architecture="brand_new_arch") == "unknown"
    )


def test_every_registry_architecture_has_a_family():
    for cap in get_all_capabilities():
        assert model_compat.family_for_architecture(cap.architecture) != "unknown", (
            cap.id
        )
