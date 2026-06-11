"""T4.3: the garden vault stops being a second brain.

API-created notes dual-write (vault markdown mirror + brain via the
pipeline with provenance); the existing vault imports idempotently; chat
context comes from brain queries (vault scan only as the no-graph
fallback); /garden/tree works (it was a latent AttributeError).
"""

import asyncio

import pytest

import p_reinforce
from knowledge_graph import KnowledgeGraphStore
from latticeai.services.ingestion import IngestionPipeline
from p_reinforce import PReinforceGardener


@pytest.fixture
def vault(tmp_path, monkeypatch):
    vault_dir = tmp_path / "vault"
    monkeypatch.setattr(p_reinforce, "BRAIN_DIR", vault_dir)
    return vault_dir


def _brain(tmp_path):
    kg = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    return kg, IngestionPipeline(kg, hooks=None, enable_graph=True)


def test_process_dual_writes_vault_and_brain(vault, tmp_path):
    kg, pipe = _brain(tmp_path)
    gardener = PReinforceGardener(ingestion_pipeline=pipe, knowledge_graph=kg)
    result = asyncio.run(gardener.process("def hello():\n    return 'world'"))

    assert result["status"] == "saved"
    assert result["folder"] == "20_Skills"
    assert (vault / "20_Skills").exists(), "markdown mirror must keep working"
    assert result["graph"] == "ok"
    prov = kg.get_provenance(result["graph_node_id"])
    assert prov is not None and prov["source_type"] == "note"


def test_import_vault_is_idempotent(vault, tmp_path):
    kg, pipe = _brain(tmp_path)
    gardener = PReinforceGardener(ingestion_pipeline=pipe, knowledge_graph=kg)
    (vault / "10_Wiki" / "note1.md").write_text("# RAG 개념\nRAG explained", encoding="utf-8")
    (vault / "00_Raw" / "note2.md").write_text("# idea\nrandom idea", encoding="utf-8")
    (vault / "40_Log" / "2026-06-11.md").write_text("# log\nskipme", encoding="utf-8")

    first = gardener.import_vault()
    assert first["imported"] == 2 and first["failed"] == 0

    second = gardener.import_vault()
    assert second["imported"] == 0, "re-import must dedupe by content hash"
    assert second["duplicates"] == 2


def test_relevant_context_queries_brain_not_vault(vault, tmp_path):
    kg, pipe = _brain(tmp_path)
    gardener = PReinforceGardener(ingestion_pipeline=pipe, knowledge_graph=kg)
    asyncio.run(gardener.process("kubernetes upgrade runbook with steps"))

    # Remove the vault copy: a brain-backed lookup still finds the note.
    for f in (vault / "20_Skills").glob("*.md"):
        f.unlink()
    for f in (vault / "00_Raw").glob("*.md"):
        f.unlink()
    context = gardener.get_relevant_context("kubernetes")
    assert "kubernetes" in context.lower(), "context must come from the brain, not a vault rescan"


def test_relevant_context_falls_back_to_vault_without_graph(vault, tmp_path):
    gardener = PReinforceGardener()  # no pipeline, no graph (graph-disabled mode)
    (vault / "10_Wiki" / "k8s.md").write_text("# k8s\nkubernetes basics", encoding="utf-8")
    context = gardener.get_relevant_context("kubernetes")
    assert "kubernetes" in context.lower()


def test_get_tree_returns_real_structure(vault, tmp_path):
    gardener = PReinforceGardener()
    (vault / "10_Wiki" / "a.md").write_text("# a", encoding="utf-8")
    tree = gardener.get_tree()
    assert tree["root"] == str(vault)
    wiki = next(f for f in tree["folders"] if f["name"] == "10_Wiki")
    assert wiki["count"] == 1 and wiki["files"][0]["name"] == "a.md"
