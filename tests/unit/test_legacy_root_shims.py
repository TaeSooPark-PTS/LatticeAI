"""Compatibility smoke tests for historical root import paths."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path


def test_legacy_root_shims_resolve_to_package_modules():
    ltcai_cli = importlib.import_module("ltcai_cli")
    telegram_bot = importlib.import_module("telegram_bot")
    p_reinforce = importlib.import_module("p_reinforce")

    assert ltcai_cli.__name__ == "latticeai.cli.entrypoint"
    assert telegram_bot.__name__ == "latticeai.integrations.telegram_bot"
    assert p_reinforce.__name__ == "latticeai.services.p_reinforce"
    assert callable(ltcai_cli.main)
    assert hasattr(telegram_bot, "run_bot")
    assert hasattr(p_reinforce, "PReinforceGardener")


def test_knowledge_graph_root_exports_store_and_schema_symbols():
    knowledge_graph = importlib.import_module("knowledge_graph")

    assert hasattr(knowledge_graph, "KnowledgeGraphStore")
    assert isinstance(knowledge_graph.GRAPH_SCHEMA_VERSION, int)
    assert "KnowledgeGraphStore" in knowledge_graph.__all__


def test_stateful_root_shims_alias_physical_modules():
    mcp_registry = importlib.import_module("mcp_registry")
    real_mcp_registry = importlib.import_module("latticeai.core.mcp_registry")
    llm_router = importlib.import_module("llm_router")
    real_llm_router = importlib.import_module("latticeai.models.router")
    workflow_engine = importlib.import_module("latticeai.core.workflow_engine")
    real_workflow_engine = importlib.import_module("lattice_brain.workflow")
    auto_setup = importlib.import_module("auto_setup")
    real_auto_setup = importlib.import_module("latticeai.setup.auto_setup")
    setup_wizard = importlib.import_module("setup_wizard")
    real_setup_wizard = importlib.import_module("latticeai.setup.wizard")
    local_knowledge = importlib.import_module("local_knowledge_api")
    real_local_knowledge = importlib.import_module("latticeai.services.local_knowledge")

    assert mcp_registry is real_mcp_registry
    assert llm_router is real_llm_router
    assert workflow_engine is real_workflow_engine
    assert auto_setup is real_auto_setup
    assert setup_wizard is real_setup_wizard
    assert local_knowledge is real_local_knowledge


def test_tools_root_shim_preserves_module_submodule_and_state_identity(tmp_path, monkeypatch):
    legacy_tools = importlib.import_module("tools")
    package_tools = importlib.import_module("latticeai.tools")
    legacy_filesystem = importlib.import_module("tools.filesystem")
    package_filesystem = importlib.import_module("latticeai.tools.filesystem")
    legacy_knowledge = importlib.import_module("tools.knowledge")
    package_knowledge = importlib.import_module("latticeai.tools.knowledge")

    assert legacy_tools is package_tools
    assert legacy_filesystem is package_filesystem
    assert legacy_knowledge is package_knowledge
    assert legacy_tools.DEFAULT_TOOL_REGISTRY is package_tools.DEFAULT_TOOL_REGISTRY

    monkeypatch.setattr(legacy_tools, "AGENT_ROOT", tmp_path)
    assert package_tools.AGENT_ROOT == tmp_path
    assert legacy_tools.ensure_agent_root() == tmp_path


def test_tools_root_package_contains_only_the_compatibility_shim():
    root = Path(__file__).resolve().parents[2] / "tools"

    assert sorted(path.name for path in root.glob("*.py")) == ["__init__.py"]


def test_latticeai_internal_modules_use_physical_tools_package():
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in (root / "latticeai").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "tools" or alias.name.startswith("tools.") for alias in node.names):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "tools" or str(node.module).startswith("tools."):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}")

    assert offenders == []


def test_internal_shim_layers_are_gone():
    """8.8.0 removed the internal-only shim layers for Brain Core extraction.

    ``lattice_brain`` must expose exactly one import surface (its physical
    module paths); the pre-graph flat modules, the deprecated
    ``latticeai.brain`` namespace, and the service-layer AgentRuntime alias
    must no longer be importable.
    """
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


def test_legacy_shim_report_tracks_removals():
    from latticeai.core.legacy_compatibility import legacy_shim_report

    report = legacy_shim_report()
    assert report["status"] == "managed"
    assert "root" in report["layers"]
    assert report["lingering"] == []
    assert report["removed_count"] >= 5
    removed_paths = {shim["path"] for shim in report["removed"]}
    assert "lattice_brain/store.py" in removed_paths
    assert "latticeai/brain/" in removed_paths
    assert "latticeai/services/agent_runtime.py" in removed_paths
    live_paths = {shim["path"] for shim in report["shims"]}
    assert live_paths.isdisjoint(removed_paths)


def test_server_root_stays_lazy_until_app_attribute_access():
    server = importlib.import_module("server")

    assert "app" in dir(server)
    assert callable(server.main)
