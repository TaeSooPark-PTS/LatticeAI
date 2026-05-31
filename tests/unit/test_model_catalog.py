"""Boundary tests for the extracted model catalog module.

``latticeai.services.model_catalog`` owns the static engine/model catalog data
that used to live inside ``model_runtime``. These tests pin two things:

* the re-export identity (``model_runtime`` exposes the *same* objects), so the
  historical ``from ...model_runtime import ENGINE_MODEL_CATALOG`` import path
  cannot silently diverge from the catalog source of truth, and
* basic catalog invariants relied on by the model APIs and recommendation flow.
"""

from latticeai.services import model_catalog
from latticeai.services import model_runtime


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
