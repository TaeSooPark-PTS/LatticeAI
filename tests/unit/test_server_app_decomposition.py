"""v1.4.0 final server_app decomposition guards."""

import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_server_app_under_final_decomposition_target():
    line_count = len((ROOT / "latticeai" / "server_app.py").read_text(encoding="utf-8").splitlines())
    # v3.2.0 wires four additional platform routers (MCP manager, hooks, agent
    # registry, memory) into the assembly file; the lean-assembly target is
    # adjusted to accommodate that real surface while still guarding against drift.
    assert line_count <= 1560


def test_v14_router_and_service_modules_import_independently():
    for module_name in (
        "latticeai.api.chat",
        "latticeai.api.computer_use",
        "latticeai.api.local_files",
        "latticeai.api.permissions",
        "latticeai.api.tools",
        "latticeai.api.garden",
        "latticeai.api.setup",
        "latticeai.api.static_routes",
        "latticeai.services.model_runtime",
        "latticeai.services.tool_dispatch",
        "latticeai.services.upload_service",
        "latticeai.services.app_context",
    ):
        assert importlib.import_module(module_name) is not None


def test_version_metadata_matches_release():
    release = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    from latticeai import __version__
    from latticeai.core.workspace_os import WORKSPACE_OS_VERSION

    assert __version__ == release
    assert release == WORKSPACE_OS_VERSION


def test_markdown_current_release_references_match_release():
    # Public release history starts at 9.0.0 since 10.10.0 — the 8.x era was
    # removed from the README, the notes index, RELEASE.md and the changelog.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    history = readme.split("## Release History", 1)[1]
    assert "9.0.0" in history
    assert "8.9.0" not in history
    assert "8.0.0" not in history
    assert "7.9.0" not in history
    assert "7.0.0" not in history
    assert "6.7.0" not in history
    assert "4.5.0" not in history
    assert "New in 1.3.0" not in readme
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    release = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    release_minor = ".".join(release.split(".")[:2])
    assert f"{release_minor}.x (latest)" in security


def test_create_mcp_install_state_focused_interface(tmp_path):
    """MCP install state focused test: verify extraction contract and basic behavior."""
    from pathlib import Path as _Path

    from latticeai.core.mcp_registry import create_mcp_install_state

    state = create_mcp_install_state(_Path(tmp_path))
    assert isinstance(state, dict)
    expected = {
        "load_mcp_installs",
        "save_mcp_installs",
        "mcp_public_item",
        "recommend_mcps",
        "install_mcp",
    }
    assert expected.issubset(state.keys())
    for k in expected:
        assert callable(state[k]), f"{k} must be callable"

    # basic roundtrip on load/save (sync parts)
    loaded = state["load_mcp_installs"]()
    assert isinstance(loaded, dict)
    assert "installed" in loaded

    # save should not raise
    state["save_mcp_installs"]({"installed": {"test": {"status": "installed"}}})

    loaded2 = state["load_mcp_installs"]()
    assert "test" in loaded2.get("installed", {})


# ── v9.9.6 extractions (review 2026-07-27 P2 #8) ─────────────────────────────
# "대형 모듈 추가 분해 (workspace_os, ingestion job/watch 분리) — 동작 보존
# 이동 우선". Both moves must be invisible to every existing importer.

def test_ingestion_job_queue_moved_but_stays_importable_from_the_pipeline():
    import lattice_brain.ingestion as pipeline
    import lattice_brain.ingestion_jobs as jobs

    assert pipeline.BackgroundIngestionQueue is jobs.BackgroundIngestionQueue
    assert pipeline.BackgroundIngestionJob is jobs.BackgroundIngestionJob
    assert pipeline.JOB_ERRORS_CAP == jobs.JOB_ERRORS_CAP
    # The job module owns scheduling only — it must not drag the pipeline in.
    assert not hasattr(jobs, "IngestionPipeline")


def test_review_queue_persistence_moved_but_store_methods_are_unchanged(tmp_path):
    from latticeai.core.workspace_os import WorkspaceOSStore
    from latticeai.core.workspace_review_items import WorkspaceReviewItems

    store = WorkspaceOSStore(tmp_path / "workspace.json")
    assert isinstance(store.review_items, WorkspaceReviewItems)

    item = store.create_review_item(title="검토 항목", source="workflow_run")
    assert item["status"] == "pending"
    assert [row["id"] for row in store.list_review_items()] == [item["id"]]
    assert store.get_review_item(item["id"])["title"] == "검토 항목"
    updated = store.update_review_item(item["id"], status="approved")
    assert updated["status"] == "approved"


def test_review_queue_scope_isolation_survives_the_move(tmp_path):
    from latticeai.core.workspace_os import WorkspaceOSStore

    store = WorkspaceOSStore(tmp_path / "workspace.json")
    item = store.create_review_item(title="a", workspace_id="team")
    with pytest.raises(FileNotFoundError):
        store.get_review_item(item["id"], workspace_id="other")
