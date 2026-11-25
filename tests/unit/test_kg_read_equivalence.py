import json

import pytest

kg = pytest.importorskip("lattice_brain.graph.store")
pytest.importorskip("lattice_brain.graph.schema")

_NODES = [
    (
        "concept:rag",
        "Concept",
        "RAG retrieval",
        "검색 증강 생성 개념",
        {"source": "doc"},
    ),
    (
        "decision:db",
        "Decision",
        "Use SQLite",
        "decided to use sqlite for storage",
        {"conversation_id": "c1"},
    ),
    (
        "task:index",
        "Task",
        "Build index",
        "index the documents nightly",
        {"owner": "me"},
    ),
    (
        "doc:spec",
        "Document",
        "Spec sheet",
        "the full spec for RAG and sqlite",
        {"filename": "spec.md"},
    ),
    (
        "file:main",
        "File",
        "main.py",
        "entry point that wires sqlite",
        {"relative_path": "src/main.py"},
    ),
    ("topic:storage", "Topic", "Storage", "storage topic", {}),
    ("person:me", "Person", "Me", "the user", {}),
    (
        "message:m1",
        "Message",
        "How does RAG work?",
        "user asked about rag",
        {"conversation_id": "c1"},
    ),
]
_EDGES = [
    ("message:m1", "concept:rag", "mentions", 1.0, {}),
    ("decision:db", "topic:storage", "discusses", 2.0, {"confidence": 0.9}),
    ("doc:spec", "concept:rag", "references", 1.5, {}),
    ("file:main", "decision:db", "related_to", 1.0, {}),
    ("topic:storage", "file:main", "mentions", 1.2, {}),
]


def _seed(store):
    with store._connect() as conn:
        for i, (nid, ntype, title, summary, meta) in enumerate(_NODES):
            ts = f"2026-01-{i + 1:02d}T00:00:00"
            conn.execute(
                "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    nid,
                    ntype,
                    title,
                    summary,
                    json.dumps(meta, ensure_ascii=False),
                    "{}",
                    ts,
                    ts,
                ),
            )
        for j, (a, b, etype, w, meta) in enumerate(_EDGES):
            conn.execute(
                "INSERT INTO edges(id,from_node,to_node,type,weight,metadata_json,created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    f"e{j}",
                    a,
                    b,
                    etype,
                    w,
                    json.dumps(meta, ensure_ascii=False),
                    f"2026-02-{j + 1:02d}T00:00:00",
                ),
            )
    store._backfill_v2_if_needed()


def _both(store, fn):
    store._read_from_v2 = False
    legacy = fn()
    store._read_from_v2 = True
    v2 = fn()
    return legacy, v2


def _without_generated_at(result):
    if isinstance(result, dict):
        return {
            key: _without_generated_at(value)
            for key, value in result.items()
            if key != "generated_at"
        }
    if isinstance(result, list):
        return [_without_generated_at(item) for item in result]
    return result


@pytest.fixture()
def store(tmp_path):
    s = kg.KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    _seed(s)
    return s


def _node_ids(result_nodes):
    return [n["id"] for n in result_nodes]


def test_search_equivalent(store):
    for q in ["rag", "sqlite", "검색", "index", "spec"]:
        legacy, v2 = _both(store, lambda: store.search(q, limit=20))
        assert _node_ids(legacy["matches"]) == _node_ids(v2["matches"]), (
            f"search({q!r}) differs"
        )


def test_context_for_query_equivalent(store):
    for q in ["rag", "sqlite storage", "검색 증강"]:
        legacy, v2 = _both(store, lambda: store.context_for_query(q, limit=6))
        assert legacy == v2, f"context_for_query({q!r}) differs"


def test_graph_equivalent(store):
    legacy, v2 = _both(store, lambda: store.graph(limit=300))
    assert _node_ids(legacy["nodes"]) == _node_ids(v2["nodes"])
    norm = lambda es: sorted((e["from"], e["to"], e["type"], e["weight"]) for e in es)
    assert norm(legacy["edges"]) == norm(v2["edges"])


def test_neighbors_equivalent(store):
    for nid in ["concept:rag", "decision:db", "file:main"]:
        legacy, v2 = _both(store, lambda: store.neighbors(nid))
        assert _node_ids(legacy["neighbors"]) == _node_ids(v2["neighbors"]), (
            f"neighbors({nid}) differs"
        )
        norm = lambda es: sorted((e["from"], e["to"], e["type"]) for e in es)
        assert norm(legacy["edges"]) == norm(v2["edges"])


def test_multi_hop_equivalent(store):
    legacy, v2 = _both(
        store, lambda: store.multi_hop_context(["message:m1"], max_hops=2)
    )
    assert _node_ids(legacy["nodes"]) == _node_ids(v2["nodes"])
    seq = lambda es: [(e["from"], e["to"], e["type"]) for e in es]
    assert seq(legacy["edges"]) == seq(v2["edges"])


def test_doc_generation_equivalent(store):
    for q in ["rag", "sqlite", "spec"]:
        legacy, v2 = _both(
            store, lambda: store.search_for_document_generation(q, limit=10)
        )
        assert [(r["id"], r["type"]) for r in legacy] == [
            (r["id"], r["type"]) for r in v2
        ], f"docgen({q!r}) differs"


def test_stats_equivalent(store):
    legacy, v2 = _both(store, store.stats)
    assert legacy["nodes"] == v2["nodes"]
    assert legacy["edges"] == v2["edges"]


def test_tied_timestamps_order_deterministically(tmp_path):
    s = kg.KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    tied = "2026-03-03T03:03:03"
    ids = [
        "concept:zeta",
        "concept:alpha",
        "concept:mu",
        "concept:beta",
        "concept:omega",
    ]
    with s._connect() as conn:
        for nid in ids:
            conn.execute(
                "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    nid,
                    "Concept",
                    "tievalue topic",
                    "tievalue body",
                    "{}",
                    "{}",
                    tied,
                    tied,
                ),
            )
    s._backfill_v2_if_needed()

    def search_ids():
        return [m["id"] for m in s.search("tievalue", limit=50)["matches"]]

    def graph_ids():
        return [n["id"] for n in s.graph(limit=300)["nodes"] if n["id"] in set(ids)]

    expected = sorted(ids)

    s._read_from_v2 = False
    legacy_search, legacy_graph = search_ids(), graph_ids()
    s._read_from_v2 = True
    v2_search, v2_graph = search_ids(), graph_ids()

    assert legacy_search == v2_search, "search order diverges under tied timestamps"
    assert v2_search == expected, "tied rows not ordered by id ASC"
    assert legacy_graph == v2_graph == expected, (
        "graph order diverges under tied timestamps"
    )
    assert search_ids() == v2_search and graph_ids() == v2_graph


def test_dual_write_keeps_v2_in_sync(store):
    with store._connect() as conn:
        store._upsert_node(
            conn, "concept:new", "Concept", "Brand New", "freshly written", {"k": "v"}
        )
    store._read_from_v2 = True
    ids = _node_ids(store.search("brand new", limit=10)["matches"])
    assert "concept:new" in ids


def test_reupsert_updates_v2_projection(store):
    with store._connect() as conn:
        store._upsert_node(
            conn,
            "concept:rag",
            "Concept",
            "RAG retrieval UPDATED",
            "summary now mentions zephyr",
            {"source": "doc", "tag": "zephyr"},
        )
    store._read_from_v2 = True
    matches = store.search("zephyr", limit=10)["matches"]
    by_id = {m["id"]: m for m in matches}
    assert "concept:rag" in by_id, "re-upserted content not visible in v2"
    assert by_id["concept:rag"]["title"] == "RAG retrieval UPDATED", (
        "v2 title not refreshed"
    )
    legacy, v2 = _both(store, lambda: store.search("zephyr", limit=10))
    assert _node_ids(legacy["matches"]) == _node_ids(v2["matches"])


def test_v2_reflects_deletes(store):
    store._read_from_v2 = True
    assert "message:m1" in _node_ids(store.search("rag", limit=20)["matches"])

    conv_id = f"conversation:{kg._slug('c1')}"
    with store._connect() as conn:
        store._upsert_node(conn, conv_id, "Conversation", "c1", "", {})
        store._upsert_edge(conn, conv_id, "message:m1", "contains", 1.0, {})

    store.delete_conversation("c1")
    store._read_from_v2 = True
    assert "message:m1" not in _node_ids(store.search("rag", limit=20)["matches"]), (
        "deleted node still visible via v2 read"
    )

    store.clear_all()
    store._read_from_v2 = True
    assert store.search("rag", limit=20)["matches"] == []
    assert store.stats()["nodes"] == {}


def test_list_documents_equivalent(store):
    legacy, v2 = _both(store, lambda: store.list_documents(limit=50))
    assert _node_ids(legacy.get("documents", [])) == _node_ids(
        v2.get("documents", [])
    ), "list_documents differs"


def test_get_node_equivalent(store):
    for nid in ["doc:spec", "concept:rag", "decision:db"]:
        legacy, v2 = _both(store, lambda: store.get_node(nid))
        assert legacy == v2, f"get_node({nid}) differs"


def test_relationship_search_equivalent(store):
    for args in [
        {"query": "rag"},
        {"node_id": "doc:spec"},
        {"relationship_type": "references"},
        {"query": "spec", "relationship_type": "ref"},
    ]:
        legacy, v2 = _both(store, lambda: store.relationship_search(**args, limit=20))
        assert legacy == v2, f"relationship_search({args}) differs"


def test_traverse_equivalent(store):
    for nid, depth in [("concept:rag", 1), ("decision:db", 2), ("file:main", 1)]:
        legacy, v2 = _both(store, lambda: store.traverse(nid, depth=depth, limit=50))
        assert _node_ids(legacy.get("nodes", [])) == _node_ids(v2.get("nodes", [])), (
            f"traverse({nid},d={depth}) nodes differ"
        )
        norm = lambda es: sorted(
            (
                e.get("from") or e.get("from_node"),
                e.get("to") or e.get("to_node"),
                e.get("type"),
            )
            for e in es
        )
        assert norm(legacy.get("edges", [])) == norm(v2.get("edges", [])), (
            f"traverse({nid}) edges differ"
        )


def test_list_documents_indexed_state_equivalent(tmp_path):
    s = kg.KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    with s._connect() as conn:
        conn.execute(
            "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                "doc:indexed",
                "Document",
                "Indexed spec",
                "indexed document",
                json.dumps({"filename": "indexed.md"}),
                "{}",
                "2026-01-01T00:00:00",
                "2026-01-01T00:00:00",
            ),
        )
        conn.execute(
            "INSERT INTO nodes(id,type,title,summary,metadata_json,raw_json,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                "chunk:doc:indexed:0",
                "Chunk",
                "chunk 0",
                "chunk body",
                json.dumps({"document_id": "doc:indexed", "ord": 0}),
                "{}",
                "2026-01-02T00:00:00",
                "2026-01-02T00:00:00",
            ),
        )
    s._backfill_v2_if_needed()

    legacy, v2 = _both(s, lambda: s.list_documents(limit=50))
    assert _without_generated_at(legacy) == _without_generated_at(v2), (
        "list_documents (indexed state) diverges under v2 toggle"
    )
    by_id = {d["id"]: d for d in v2.get("documents", [])}
    assert by_id["doc:indexed"]["indexed"] is True, (
        "Chunk not counted via v2 projection"
    )
    assert by_id["doc:indexed"]["chunks"] == 1


def test_get_node_missing_raises_on_both_paths(store):
    for read_v2 in (False, True):
        store._read_from_v2 = read_v2
        with pytest.raises(ValueError):
            store.get_node("concept:does-not-exist")
