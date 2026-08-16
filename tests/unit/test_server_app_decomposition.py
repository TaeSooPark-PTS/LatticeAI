"""Worker-only composition guards. Release-history assertions stay untouched."""

import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_worker_app_is_the_only_application_factory():
    source = (ROOT / "latticeai" / "worker_app.py").read_text(encoding="utf-8")
    assert "def create_worker_app" in source
    assert not (ROOT / "latticeai" / "server_app.py").exists()


def test_v14_router_and_service_modules_import_independently():
    for module_name in (
        "latticeai.api.agent_worker_seam",
        "latticeai.api.models",
        "latticeai.api.health",
        "latticeai.api.search",
        "latticeai.api.worker_compute",
        "latticeai.api.worker_seams",
        "latticeai.services.model_runtime",
        "latticeai.services.tool_dispatch",
        "latticeai.services.search_service",
        "latticeai.worker_app",
    ):
        assert importlib.import_module(module_name) is not None


def test_version_metadata_matches_release():
    release = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    from latticeai import __version__

    assert __version__ == release


def test_markdown_current_release_references_match_release():
    # Public release history starts at 11.0.0 since 11.6.0. The floor is the
    # support policy, not housekeeping: 11.6.0 ("One Door") rebuilt the product
    # server in Rust and reduced the Python package to a pure-compute AI worker,
    # so a 10.x or 9.x install is a different program and SECURITY.md now says
    # only 11.x receives fixes. The README table states the same boundary, and
    # this assertion is what keeps the two from drifting apart. (10.10.0 moved
    # this floor 8.0.0 → 9.0.0 the same way, test and doc gate together.)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    history = readme.split("## Release History", 1)[1]
    assert "11.0.0" in history
    for retired in ("10.10.0", "10.0.0", "9.9.9", "9.0.0", "8.9.0", "8.0.0"):
        assert retired not in history, f"{retired} is below the supported floor"
    assert "New in 1.3.0" not in readme
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    release = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    release_minor = ".".join(release.split(".")[:2])
    assert f"{release_minor}.x (latest)" in security


def test_worker_app_factory_is_importable_without_create_app():
    worker = importlib.import_module("latticeai.worker_app")
    factory = importlib.import_module("latticeai.app_factory")
    assert hasattr(worker, "create_worker_app")
    assert hasattr(factory, "build_context")
    assert not hasattr(factory, "create_app")


def test_the_ingestion_package_is_vocabulary_and_compute_only():
    """No write door, and since v11.8.0 no capability probe either."""
    import lattice_brain.ingestion as ingestion

    assert hasattr(ingestion, "assess_extraction_quality")
    assert hasattr(ingestion, "content_hash_text")
    assert not hasattr(ingestion, "IngestionPipeline")
    assert not hasattr(ingestion, "BackgroundIngestionQueue")


def test_review_queue_is_no_longer_a_python_store():
    import importlib.util

    assert importlib.util.find_spec("latticeai.core.workspace_os") is None
    assert importlib.util.find_spec("latticeai.core.workspace_review_items") is None
