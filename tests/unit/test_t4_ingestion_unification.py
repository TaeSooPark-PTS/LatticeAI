"""T4.1: chat/MCP messages flow through the unified ingestion pipeline.

Every source through one door: conversational exchanges now carry
ingestion_provenance and fire the kg_ingest hook lifecycle like files and
web pages. provenance_coverage() is the honesty metric for the claim.
"""

from knowledge_graph import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionItem, IngestionPipeline


def _pipeline(tmp_path):
    kg = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    return kg, IngestionPipeline(kg, hooks=None, enable_graph=True)


def test_chat_message_through_pipeline_records_provenance(tmp_path):
    kg, pipe = _pipeline(tmp_path)
    result = pipe.ingest(
        IngestionItem(
            source_type="chat_message",
            text="프로젝트 일정 공유",
            owner="alice@x.com",
            conversation_id="conv-1",
            metadata={"role": "user", "source": "web"},
        ),
        user_email="alice@x.com",
    )
    assert result.status == "ok"
    assert result.node_id, "chat ingest must yield the message node id"
    prov = kg.get_provenance(result.node_id)
    assert prov is not None, "chat messages must carry provenance now"
    assert prov["source_type"] == "chat_message"
    assert prov["owner"] == "alice@x.com"


def test_chat_message_through_pipeline_projects_workspace_scope(tmp_path):
    kg, pipe = _pipeline(tmp_path)
    result = pipe.ingest(
        IngestionItem(
            source_type="chat_message",
            text="org roadmap decision",
            owner="alice@x.com",
            workspace_id="org:acme",
            conversation_id="conv-org",
            metadata={"role": "user", "source": "web"},
        ),
        user_email="alice@x.com",
    )

    assert result.status == "ok"
    assert kg.workspaces_of([result.node_id]).get(result.node_id) == "org:acme"
    assert kg.workspaces_of(["conversation:conv-org"]).get("conversation:conv-org") == "org:acme"
    assert kg.filter_scoped_nodes([{"id": result.node_id}], {"org:other"}) == []
    assert kg.filter_scoped_nodes([{"id": result.node_id}], {"org:acme"}) == [{"id": result.node_id}]
    assert all(node["id"] != "conversation:conv-org" for node in kg.graph(allowed_workspaces={"org:other"})["nodes"])
    assert any(node["id"] == "conversation:conv-org" for node in kg.graph(allowed_workspaces={"org:acme"})["nodes"])


def test_mcp_message_through_pipeline_records_provenance(tmp_path):
    kg, pipe = _pipeline(tmp_path)
    result = pipe.ingest(
        IngestionItem(
            source_type="mcp_message",
            text="external agent says hello",
            owner="bot@x.com",
            metadata={"role": "assistant", "source": "mcp"},
        ),
        user_email="bot@x.com",
    )
    assert result.status == "ok"
    prov = kg.get_provenance(result.node_id)
    assert prov is not None and prov["source_type"] == "mcp_message"


def test_assistant_role_creates_airesponse_node(tmp_path):
    kg, pipe = _pipeline(tmp_path)
    result = pipe.ingest(
        IngestionItem(
            source_type="chat_message",
            text="the answer is 42",
            owner="alice@x.com",
            metadata={"role": "assistant"},
        ),
    )
    assert result.node_id.startswith("airesponse:")


def test_provenance_coverage_metric(tmp_path):
    kg, pipe = _pipeline(tmp_path)
    # One covered write (through the pipeline) …
    pipe.ingest(IngestionItem(source_type="chat_message", text="covered", metadata={"role": "user"}))
    # … and one legacy bypass (direct store call, no provenance).
    kg.ingest_message("user", "uncovered legacy write", user_email="a@b.c")

    cov = kg.provenance_coverage()
    assert cov["total_nodes"] >= 2
    assert cov["nodes_with_provenance"] >= 1
    assert cov["coverage_ratio"] is not None and 0 < cov["coverage_ratio"] < 1, (
        "metric must honestly show partial coverage when a bypass write exists"
    )
    assert "chat_message" in cov["provenance_by_source_type"]
    assert cov["uncovered_by_type"], "uncovered nodes are reported by type"


# ── T4.4: graph_curator goes live ──────────────────────────────────────────

def test_curate_promotes_multi_source_topics(tmp_path):
    kg, pipe = _pipeline(tmp_path)
    # Same strong topic in 3 separate sources — passes the min_sources gate.
    for i in range(3):
        pipe.ingest(IngestionItem(
            source_type="chat_message",
            text=f"kubernetes cluster upgrade discussion number {i} about kubernetes cluster",
            metadata={"role": "user"},
        ))
    result = kg.curate()
    assert result["status"] == "ok"
    assert result["documents_scanned"] >= 3
    labels = [p["label"] for p in result["promoted"]]
    assert any("kubernetes" in label for label in labels), (labels, result["skipped"][:5])
    promoted = next(p for p in result["promoted"] if "kubernetes" in p["label"])
    assert promoted["linked_sources"] >= 2
    # importance_score is REAL in nodes_v2 now.
    with kg._connect() as conn:
        score = conn.execute(
            "SELECT importance_score FROM nodes_v2 WHERE id=?", (promoted["node_id"],)
        ).fetchone()[0]
    assert score > 0


def test_curate_reports_skips_honestly(tmp_path):
    kg, pipe = _pipeline(tmp_path)
    pipe.ingest(IngestionItem(source_type="chat_message", text="solitary mention of xyzzy", metadata={"role": "user"}))
    result = kg.curate()
    assert result["skipped_total"] >= 0
    assert isinstance(result["skipped"], list), "skips must be visible, not silent"
