"""v1.4.0 final server_app decomposition guards."""

from pathlib import Path
import importlib


ROOT = Path(__file__).resolve().parents[2]


def test_server_app_under_final_decomposition_target():
    line_count = len((ROOT / "latticeai" / "server_app.py").read_text(encoding="utf-8").splitlines())
    assert line_count <= 1500


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
    # Rolling release-version guard — bumped each release.
    from latticeai import __version__
    from latticeai.core.workspace_os import WORKSPACE_OS_VERSION

    assert __version__ == "2.2.4"
    assert WORKSPACE_OS_VERSION == "2.2.4"


def test_markdown_current_release_references_match_release():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    history = readme.split("## Release history", 1)[1]
    # README is intentionally frozen for the v2.2.4 stabilization release (no
    # README/marketplace copy changes this cycle); release notes live in
    # CHANGELOG/RELEASE/RELEASE_NOTES. Guard the current minor series instead.
    assert "2.2" in history
    assert "New in 1.3.0" not in readme
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "2.2.x (latest)" in security
