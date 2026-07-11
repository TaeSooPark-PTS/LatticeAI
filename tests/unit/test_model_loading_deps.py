"""Guards for model loading runtime dependency exports."""

from latticeai.services.model_loading import _get_model_runtime_deps
from latticeai.services.model_runtime import ModelRuntimeState


def test_model_loading_runtime_deps_import_cleanly():
    deps = _get_model_runtime_deps(ModelRuntimeState())

    assert callable(deps["_friendly_model_runtime_error"])
    assert callable(deps["_model_runtime_compatibility"])
    assert callable(deps["_smoke_test_loaded_model"])
