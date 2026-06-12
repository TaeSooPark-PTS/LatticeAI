"""v1.4.0 final server_app decomposition guards."""

from pathlib import Path
import importlib
import json


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
    assert WORKSPACE_OS_VERSION == release


def test_markdown_current_release_references_match_release():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    history = readme.split("## Release History", 1)[1]
    assert "3.0.0" in history
    assert "New in 1.3.0" not in readme
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    release = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    release_minor = ".".join(release.split(".")[:2])
    assert f"{release_minor}.x (latest)" in security
