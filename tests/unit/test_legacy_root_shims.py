"""Legacy debt gate — the root shim layer stays removed.

9.9.1 deleted every root compatibility shim except ``server.py`` (kept for
``uvicorn server:app``, the Docker CMD, and launch scripts). This suite is the
regression gate: removed shims must stay unimportable, no new root modules may
appear, and the managed registry in ``latticeai.core.legacy_compatibility``
must keep matching reality.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The only Python modules allowed at the repository root.
ALLOWED_ROOT_MODULES = {"server.py"}

REMOVED_ROOT_MODULES = [
    "ltcai_cli",
    "auto_setup",
    "setup_wizard",
    "mcp_registry",
    "kg_schema",
    "knowledge_graph",
    "knowledge_graph_api",
    "local_knowledge_api",
    "llm_router",
    "p_reinforce",
    "telegram_bot",
    "tools",
]


def test_removed_root_shims_are_not_importable():
    for module_name in REMOVED_ROOT_MODULES:
        try:
            importlib.import_module(module_name)
            raise AssertionError(f"removed root shim {module_name} is importable again")
        except ImportError:
            pass


def test_repo_root_contains_no_new_python_modules():
    root_modules = {path.name for path in REPO_ROOT.glob("*.py")}
    unexpected = root_modules - ALLOWED_ROOT_MODULES
    assert unexpected == set(), (
        f"new root-level Python modules appeared: {sorted(unexpected)} — "
        "put code in latticeai/ or lattice_brain/ instead of the repo root"
    )
    assert root_modules >= ALLOWED_ROOT_MODULES


def test_canonical_replacements_still_import():
    for module_name in (
        "latticeai.cli.entrypoint",
        "latticeai.setup.auto_setup",
        "latticeai.setup.wizard",
        "latticeai.core.mcp_registry",
        "lattice_brain.graph.schema",
        "lattice_brain.graph.store",
        "latticeai.api.knowledge_graph",
        "latticeai.services.local_knowledge",
        "latticeai.models.router",
        "latticeai.services.p_reinforce",
        "latticeai.integrations.telegram_bot",
        "latticeai.tools",
        "latticeai.tools.knowledge",
    ):
        importlib.import_module(module_name)


def test_latticeai_internal_modules_use_physical_tools_package():
    offenders = []
    for path in (REPO_ROOT / "latticeai").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "tools" or alias.name.startswith("tools.") for alias in node.names):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "tools" or str(node.module).startswith("tools."):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == []


def test_no_module_aliases_itself_to_a_moved_implementation():
    """11.5.2 deleted the six ``sys.modules[__name__] = _impl`` shims.

    That pattern makes a module a silent alias for one that physically moved,
    so an import path can outlive its file and both `latticeai/` and
    `lattice_brain/` grow a second name for the same code. Real callers use
    the physical path; this gate keeps it that way.
    """
    offenders = []
    for package in ("latticeai", "lattice_brain"):
        for path in (REPO_ROOT / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "modules"
                        and isinstance(target.value.value, ast.Name)
                        and target.value.value.id == "sys"
                        and isinstance(target.slice, ast.Name)
                        and target.slice.id == "__name__"
                    ):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert offenders == [], (
        "moved-module shims reappeared: "
        f"{offenders} — import the physical module instead of aliasing "
        "sys.modules[__name__]"
    )


def test_internal_shim_layers_are_gone():
    """8.8.0 removed the internal-only shim layers for Brain Core extraction."""
    removed = [
        "lattice_brain.store",
        "lattice_brain.ingest",
        "lattice_brain.retrieval",
        "lattice_brain.schema",
        "lattice_brain.provenance",
        "latticeai.brain",
        "latticeai.services.agent_runtime",
    ]
    for module_name in removed:
        try:
            importlib.import_module(module_name)
            raise AssertionError(f"removed shim {module_name} is still importable")
        except ImportError:
            pass


def test_legacy_shim_report_matches_reality():
    from latticeai.core.legacy_compatibility import legacy_shim_report

    report = legacy_shim_report()
    assert report["status"] == "managed"
    assert report["missing"] == []
    assert report["lingering"] == []
    assert report["remaining_count"] == 1
    live_paths = {shim["path"] for shim in report["shims"]}
    assert live_paths == {"server.py"}
    removed_paths = {shim["path"] for shim in report["removed"]}
    for module_name in REMOVED_ROOT_MODULES:
        expected = "tools/" if module_name == "tools" else f"{module_name}.py"
        assert expected in removed_paths, f"registry is missing removed shim {expected}"
    assert live_paths.isdisjoint(removed_paths)


def test_server_root_stays_lazy_until_app_attribute_access():
    server = importlib.import_module("server")

    assert "app" in dir(server)
    assert callable(server.main)
