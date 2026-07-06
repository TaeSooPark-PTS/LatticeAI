"""Compatibility smoke tests for historical root import paths."""

from __future__ import annotations

import importlib


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

    assert mcp_registry is real_mcp_registry
    assert llm_router is real_llm_router
    assert workflow_engine is real_workflow_engine


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
