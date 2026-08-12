"""11.2.0 model registry: the recommended/recognised split and the new families.

Two questions run through this file:

* **What do we offer?** Only the current generation, and only repos that were
  measured against the Hub — no 404s, nothing gated, exact casing.
* **What do we still understand?** Everything a user might already have on disk,
  so retiring a generation never turns their download into an unknown blob.
"""

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
    """The current-generation slice of the registry.

    The registry exposes the whole list and the legacy list; "recommended" is
    the lifecycle field, so the tests derive it rather than the product
    carrying an accessor only tests called.
    """
    return [c for c in get_all_capabilities() if c.lifecycle == RECOMMENDED]

# ── the two lists ─────────────────────────────────────────────────────────────

def test_deleted_models_are_gone_from_every_list():
    """404 / gated / non-MLX entries are deleted outright, not demoted.

    Recognising a model nobody can obtain is noise, not compatibility — it would
    put a name and a size on something that can never load.
    """
    caps = get_all_capabilities()
    ids = {c.id for c in caps} | {c.hf_repo_id for c in caps}
    for gone in (
        "mlx-community/phi-3.5-vision-4bit",          # 404 on the Hub
        "mlx-community/moondream2-4bit",              # 404 on the Hub
        "google/gemma-3-4b-it",                       # gated
        "google/gemma-3-12b-it",                      # gated
        "meta-llama/Llama-3.2-11B-Vision-Instruct",   # gated
        "mistralai/Pixtral-12B-2409",                 # vllm-only, no config.json
        "Qwen/Qwen2.5-VL-7B-Instruct",                # superseded, not an MLX build
    ):
        assert gone not in ids, gone


def test_recognized_and_recommended_are_separate_lists():
    recommended = _recommended()
    legacy = get_legacy_capabilities()

    assert all(c.lifecycle == RECOMMENDED for c in recommended)
    assert all(c.lifecycle == LEGACY for c in legacy)
    assert not {c.id for c in recommended} & {c.id for c in legacy}
    assert get_all_capabilities() == [*recommended, *legacy]

    # A superseded model already on disk stays recognised but is never offered.
    superseded = "mlx-community/Qwen3-VL-8B-Instruct-4bit"
    assert is_recognized_model(superseded) is True
    assert get_capability(superseded).lifecycle == LEGACY

    current = "mlx-community/gemma-4-12B-it-4bit"
    assert is_recognized_model(current) is True
    assert get_capability(current).lifecycle == RECOMMENDED

    assert is_recognized_model("nobody/never-existed") is False
    assert get_capability("nobody/never-existed") is None


def test_every_entry_pins_an_architecture_and_a_measured_size():
    """The static loadability verdict needs both, so neither may be blank."""
    for cap in get_all_capabilities():
        assert cap.architecture, cap.id
        assert cap.download_size_gb and cap.download_size_gb > 0, cap.id
        # The display string and the measured number must agree to 0.2GB. This
        # is what caught Llama 4 Scout claiming 11.8GB for a 61GB repo.
        shown = float(cap.size.rstrip("GB"))
        assert abs(shown - cap.download_size_gb) <= 0.2, (cap.id, cap.size)


def test_the_registry_id_is_the_hub_canonical_casing():
    """`gemma-4-12b-it-4bit` is what people type; `-12B-` is what the Hub calls it.

    Storing the canonical form keeps the download path, the on-disk cache
    directory and the catalog key identical.
    """
    cap = get_capability("mlx-community/gemma-4-12B-it-4bit")

    assert cap is not None
    assert cap.id == cap.hf_repo_id == "mlx-community/gemma-4-12B-it-4bit"
    assert model_catalog.MODEL_ENGINE_ALIASES["gemma-4-12b-it-4bit"]["local_mlx"] == cap.id


# ── engine routing ────────────────────────────────────────────────────────────

def test_catalog_ids_are_reachable_engine_targets():
    """Every non-MLX id must be a real per-engine repo, not a fabricated one.

    Until 11.2.0 an entry without an alias got an invented GGUF path built from
    its family name (``ggml-org/<family>-12B-it-GGUF``), which 404s for anything
    but Gemma 4 12B.
    """
    for engine, models in model_catalog.ENGINE_MODEL_CATALOG.items():
        if engine == "local_mlx":
            continue
        for model in models:
            model_id = str(model["id"])
            assert model_id.startswith(f"{engine}:"), model_id
            target = model_id.split(":", 1)[1]
            routes = model_catalog.MODEL_ENGINE_ALIASES[str(model["hf_repo_id"]).lower()]
            assert target == routes[engine], (engine, model_id)


def test_no_engine_route_points_at_a_gated_repo():
    """A gated target cannot be downloaded, so offering it is a dead end.

    The retired Llama 4 Scout routes pointed vLLM and LM Studio at
    ``meta-llama/Llama-4-Scout-17B-16E-Instruct``, which is gated.
    """
    gated_owners = ("meta-llama/", "google/gemma-3")
    for routes in model_catalog.MODEL_ENGINE_ALIASES.values():
        for engine, target in routes.items():
            assert not target.startswith(gated_owners), (engine, target)


def test_an_unrouted_entry_keeps_its_own_id_rather_than_inventing_one():
    """Defensive path: a provider hint added without a matching engine route.

    ``test_catalog_ids_are_reachable_engine_targets`` guards the invariant for
    everything that ships, so this can only fire on a half-finished edit. When
    it does, the entry keeps its plain id — the one thing we know is real —
    instead of a fabricated repo path that would 404 at download time.
    """
    entry = model_catalog._normalize_engine_entry(
        "ollama", {"id": "org/no-routes", "hf_repo_id": "org/no-routes", "size": "9GB"},
    )

    assert entry["id"] == "org/no-routes"
    assert entry["size"] == "실행 도구에서 관리"


def test_a_common_misname_still_resolves():
    """There is only one Gemma 4 26B, so "gemma-4-26b-it-4bit" is unambiguous."""
    routes = model_catalog.MODEL_ENGINE_ALIASES["gemma-4-26b-it-4bit"]

    assert routes["local_mlx"] == "mlx-community/gemma-4-26b-a4b-it-4bit"


# ── family filtering ──────────────────────────────────────────────────────────

def test_one_generation_may_ship_two_minor_releases():
    """Qwen3.5 9B and Qwen3.6 27B fill different RAM tiers and must coexist.

    Comparing minor versions made the smaller one vanish the moment the larger
    one joined; the filter is only meant to hide older *generations*.
    """
    ids = {m["id"] for m in model_catalog.ENGINE_MODEL_CATALOG["local_mlx"]}

    assert "mlx-community/Qwen3.5-9B-MLX-4bit" in ids
    assert "mlx-community/Qwen3.6-27B-4bit" in ids

    rows = [
        {"id": "x/Qwen2.5-VL-7B", "family": "Qwen2.5-VL", "name": "Qwen2.5-VL 7B"},
        {"id": "x/Qwen3.5-9B", "family": "Qwen3.5", "name": "Qwen3.5 9B"},
        {"id": "x/Qwen3.6-27B", "family": "Qwen3.6", "name": "Qwen3.6 27B"},
    ]
    kept = [row["id"] for row in model_catalog.filter_lower_family_versions(rows)]

    # The older generation goes; the two current minors both stay.
    assert kept == ["x/Qwen3.5-9B", "x/Qwen3.6-27B"]


# ── model_compat: new families and the architecture map ───────────────────────

def test_the_new_local_families_are_detected_from_their_ids():
    assert model_compat.detect_model_family("mlx-community/gpt-oss-20b-MXFP4-Q8") == "gpt_oss"
    assert model_compat.detect_model_family("mlx-community/LFM2.5-2.6B-4bit") == "lfm2"
    # gpt-oss must not be swallowed by the cloud "gpt" pattern that follows it.
    assert model_compat.detect_model_family("openai:gpt-4o-mini") == "gpt"


def test_the_new_families_have_their_own_profiles():
    gpt_oss = model_compat.get_model_profile("mlx-community/gpt-oss-20b-MXFP4-Q8", "local_mlx")
    lfm2 = model_compat.get_model_profile("mlx-community/LFM2.5-2.6B-4bit", "local_mlx")

    # Both are text-only: claiming vision would make the UI offer image chat.
    assert gpt_oss["supports_vision"] is False
    assert lfm2["supports_vision"] is False
    # gpt-oss speaks the harmony format, so its channel markers must stop it.
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
    # `mllama` is what the loader dispatches on; the id says nothing useful.
    assert model_compat.detect_model_family("org/mystery-4bit", architecture="mllama") == "llama"
    # An architecture we do not know must not erase the id's own evidence.
    assert model_compat.detect_model_family(
        "mlx-community/gemma-4-12B-it-4bit", architecture="brand_new_arch",
    ) == "gemma"
    # …and with neither signal, "unknown" is the honest answer.
    assert model_compat.detect_model_family("", architecture="brand_new_arch") == "unknown"


def test_every_registry_architecture_has_a_family():
    """A recognised model must never fall back to the vision-less "unknown" profile."""
    for cap in get_all_capabilities():
        assert model_compat.family_for_architecture(cap.architecture) != "unknown", cap.id
