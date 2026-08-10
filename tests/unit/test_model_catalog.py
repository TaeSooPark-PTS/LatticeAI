"""Boundary tests for the extracted model catalog module.

``latticeai.services.model_catalog`` owns the static engine/model catalog data
that used to live inside ``model_runtime``. These tests pin two things:

* the re-export identity (``model_runtime`` exposes the *same* objects), so the
  historical ``from ...model_runtime import ENGINE_MODEL_CATALOG`` import path
  cannot silently diverge from the catalog source of truth, and
* basic catalog invariants relied on by the model APIs and recommendation flow.
"""

from latticeai.services import model_catalog, model_runtime


def test_catalog_reexport_identity():
    # model_runtime must re-export the *same* objects, not copies.
    assert model_runtime.ENGINE_MODEL_CATALOG is model_catalog.ENGINE_MODEL_CATALOG
    assert model_runtime.MODEL_ENGINE_ALIASES is model_catalog.MODEL_ENGINE_ALIASES
    assert model_runtime.ENGINE_INSTALLERS is model_catalog.ENGINE_INSTALLERS
    assert model_runtime.filter_lower_family_versions is model_catalog.filter_lower_family_versions


def test_catalog_engines_present():
    catalog = model_catalog.ENGINE_MODEL_CATALOG
    for engine in ("local_mlx", "ollama", "vllm", "lmstudio", "llamacpp"):
        assert engine in catalog
        assert catalog[engine], f"{engine} catalog is empty"


def test_catalog_entries_have_required_keys():
    for engine, models in model_catalog.ENGINE_MODEL_CATALOG.items():
        for model in models:
            for key in ("id", "name", "family", "tag"):
                assert key in model, f"{engine} entry missing {key}: {model}"
            for key in ("source_country", "source_company", "execution_method", "internet_requirement", "modality"):
                assert model.get(key), f"{engine} entry missing source policy {key}: {model}"
            # Text-only entries are catalogued too (the ultralight and
            # general-purpose tiers), so each row must state which it is.
            assert model["modality"] in {"multimodal", "text"}
            assert model["architecture"], f"{engine} entry missing architecture: {model}"
            assert model["lifecycle"] == "recommended"


def test_catalog_ids_unique_per_engine():
    for engine, models in model_catalog.ENGINE_MODEL_CATALOG.items():
        ids = [m["id"] for m in models]
        assert len(ids) == len(set(ids)), f"duplicate ids in {engine}"


def test_filter_lower_family_versions_never_grows_and_is_idempotent():
    fn = model_catalog.filter_lower_family_versions
    for engine, models in model_catalog.ENGINE_MODEL_CATALOG.items():
        once = fn(models)
        assert len(once) <= len(models)
        # idempotent: filtering an already-filtered list is a no-op
        assert [m["id"] for m in fn(once)] == [m["id"] for m in once]


def test_catalog_omits_superseded_generation_models():
    """Retired generations must not reach the picker through any engine.

    ``gpt-oss`` is deliberately *not* in this list any more: GPT-OSS 20B is a
    current recommended entry, and the old list banned it as "text-only".
    """
    all_ids = {
        str(model["id"]).lower()
        for models in model_catalog.ENGINE_MODEL_CATALOG.values()
        for model in models
    }
    blocked_fragments = (
        "gemma-3", "gemma3", "gemma-2",
        "qwen2.5", "qwen3-vl",
        "llama-3", "llama-4", "llama4",
        "smollm", "phi-", "mistral", "pixtral", "moondream", "deepseek",
    )
    for fragment in blocked_fragments:
        assert not any(fragment in model_id for model_id in all_ids), fragment
