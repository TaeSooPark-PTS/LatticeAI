"""T6: graph reads honor workspace scope — Personal/Org Brain becomes real.

Search (keyword/vector/graph/hybrid) and the graph view drop rows scoped to
workspaces the caller is not a member of. Legacy-global rows (NULL scope) are
private in scoped mode unless the compatibility flag is explicit; no scoping
is applied in single-user mode (allowed=None).
"""

from knowledge_graph import KnowledgeGraphStore
from latticeai.services.search_service import SearchService


def _seeded(tmp_path):
    kg = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    with kg._connect() as conn:
        kg._upsert_node(conn, "n-acme", "Document", "quarterly secrets acme", "shared inside acme", {},
                        workspace_id="org-acme", visibility="workspace")
        kg._upsert_node(conn, "n-zeta", "Document", "quarterly secrets zeta", "shared inside zeta", {},
                        workspace_id="org-zeta", visibility="workspace")
        kg._upsert_node(conn, "n-legacy", "Document", "quarterly secrets legacy", "machine-global", {})
    return kg


def test_keyword_search_filters_by_membership(tmp_path):
    kg = _seeded(tmp_path)
    svc = SearchService(graph_store=kg)
    ids = {m["id"] for m in svc.keyword_search("quarterly", allowed_workspaces={"org-acme"})["matches"]}
    assert ids == {"n-acme"}, "other workspaces and legacy-global rows must not leak"


def test_hybrid_search_filters_by_membership(tmp_path):
    kg = _seeded(tmp_path)
    svc = SearchService(graph_store=kg)
    ids = {m["id"] for m in svc.hybrid_search("quarterly", allowed_workspaces={"org-zeta"})["matches"]}
    assert "n-acme" not in ids
    assert ids == {"n-zeta"}


def test_no_scope_means_no_filter(tmp_path):
    kg = _seeded(tmp_path)
    svc = SearchService(graph_store=kg)
    ids = {m["id"] for m in svc.keyword_search("quarterly", allowed_workspaces=None)["matches"]}
    assert ids == {"n-acme", "n-zeta", "n-legacy"}


def test_graph_view_filters_nodes_and_edges(tmp_path):
    kg = _seeded(tmp_path)
    with kg._connect() as conn:
        kg._upsert_edge(conn, "n-acme", "n-legacy", "mentions", 1.0, {})
        kg._upsert_edge(conn, "n-zeta", "n-legacy", "mentions", 1.0, {})
    view = kg.graph(limit=100, allowed_workspaces={"org-acme"})
    ids = {n["id"] for n in view["nodes"]}
    assert ids == {"n-acme"}
    for edge in view["edges"]:
        assert edge["from"] in ids and edge["to"] in ids, "edges to hidden nodes must vanish"


def test_member_of_no_org_sees_no_scoped_or_legacy_rows(tmp_path):
    kg = _seeded(tmp_path)
    svc = SearchService(graph_store=kg)
    ids = {m["id"] for m in svc.keyword_search("quarterly", allowed_workspaces=set())["matches"]}
    assert ids == set()


def test_legacy_global_compatibility_requires_explicit_opt_in(tmp_path):
    kg = _seeded(tmp_path)
    svc = SearchService(graph_store=kg)

    ids = {
        match["id"]
        for match in svc.keyword_search(
            "quarterly",
            allowed_workspaces={"org-acme"},
            include_legacy_global=True,
        )["matches"]
    }

    assert ids == {"n-acme", "n-legacy"}
