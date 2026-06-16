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


def test_server_root_stays_lazy_until_app_attribute_access():
    server = importlib.import_module("server")

    assert "app" in dir(server)
    assert callable(server.main)
