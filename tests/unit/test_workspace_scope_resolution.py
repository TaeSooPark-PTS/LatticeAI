"""One rule for "which workspace is this request talking about?".

``latticeai/api/workspace_scope.py`` replaced per-router copies of the
header/query/body derivation. Copies drift: some accepted the query parameter,
some only the header, and only three checked that a body's ``workspace_id``
agreed with the header instead of silently letting one selector win. These
tests pin the shared rule and the two behaviours the migration changed:

1. Selector agreement is enforced for every caller of ``requested_workspace``,
   including the ungated ``workspace_service=None`` router contract.
2. ``read`` and ``write`` go through *different* gates — the whole reason the
   resolver takes a ``write`` flag rather than always calling the write gate.
3. ``/knowledge-graph/stats`` and ``/knowledge-graph/schema`` counted every row
   in the store, so a member of one organization workspace could read
   another's node/edge/document volume off a "harmless" metrics endpoint.
   Those two endpoints are now scoped; the regression tests below seed two
   real workspaces and assert one cannot count the other.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionPipeline
from latticeai.api.browser import create_browser_router
from latticeai.api.knowledge_graph import create_knowledge_graph_router
from latticeai.api.workspace_scope import (
    WORKSPACE_HEADER,
    WORKSPACE_PARAM,
    requested_workspace,
    resolve_workspace_scope,
    workspace_scope_from_request,
)

ACME = "org-acme"
ZETA = "org-zeta"


# ── request fixtures ─────────────────────────────────────────────────────────
def _request(*, header: Optional[str] = None, query: Optional[str] = None) -> Request:
    """A real ASGI ``Request`` so header lookup stays case-insensitive."""
    headers = []
    if header is not None:
        headers.append((WORKSPACE_HEADER.lower().encode(), header.encode()))
    query_string = b""
    if query is not None:
        query_string = urlencode({WORKSPACE_PARAM: query}).encode()
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": headers,
            "query_string": query_string,
        }
    )


class _RecordingWorkspaceService:
    """Records which gate ran, and encodes it into the returned scope.

    The return value carries the gate name so a test cannot pass by looking
    only at ``calls`` — the value the handler would actually write with has to
    come from the right resolver.
    """

    def __init__(self, *, deny: Optional[str] = None) -> None:
        self.calls: List[Tuple[str, Optional[str], Optional[str]]] = []
        self._deny = deny

    def _gate(self, mode: str, requested: Optional[str], user: Optional[str]) -> str:
        self.calls.append((mode, requested, user))
        if self._deny is not None and requested == self._deny:
            raise PermissionError(f"{user} cannot {mode} workspace {requested}")
        return f"{mode}::{requested or 'active'}"

    def resolve_read_scope(self, requested: Optional[str], user: Optional[str]) -> str:
        return self._gate("read", requested, user)

    def resolve_write_scope(self, requested: Optional[str], user: Optional[str]) -> str:
        return self._gate("write", requested, user)


# ── 1. requested_workspace: what did the caller name? ────────────────────────
def test_header_only_selector_resolves():
    assert requested_workspace(_request(header=ACME)) == ACME


def test_query_only_selector_resolves():
    assert requested_workspace(_request(query=ACME)) == ACME


def test_body_only_selector_resolves():
    assert requested_workspace(_request(), body_workspace=ACME) == ACME


def test_all_three_agreeing_selectors_resolve_and_are_trimmed():
    resolved = requested_workspace(
        _request(header=f"  {ACME}  ", query=ACME),
        body_workspace=f"{ACME}\n",
    )
    assert resolved == ACME


def test_header_and_query_disagreement_is_403():
    with pytest.raises(HTTPException) as excinfo:
        requested_workspace(_request(header=ACME, query=ZETA))
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Workspace selectors must match."


def test_body_and_header_disagreement_is_403():
    with pytest.raises(HTTPException) as excinfo:
        requested_workspace(_request(header=ACME), body_workspace=ZETA)
    assert excinfo.value.status_code == 403


def test_body_and_query_disagreement_is_403():
    with pytest.raises(HTTPException) as excinfo:
        requested_workspace(_request(query=ACME), body_workspace=ZETA)
    assert excinfo.value.status_code == 403


def test_blank_selectors_are_absent_not_a_mismatch():
    # A client that always sends the header, empty when unset, must not be
    # rejected for "naming two workspaces" — "" and "   " name nothing.
    assert requested_workspace(_request(header="   ", query=ACME)) == ACME
    assert requested_workspace(_request(header=ACME, query=""), body_workspace="  ") == ACME
    assert requested_workspace(_request(header="", query="   "), body_workspace="") is None


def test_naming_nothing_resolves_to_none():
    # None is the pre-1.1 single-workspace path: the service falls back to the
    # active workspace. It must not become a mismatch or a made-up default.
    assert requested_workspace(_request()) is None
    assert requested_workspace(_request(), body_workspace=None) is None


def test_header_query_agreement_is_case_sensitive_on_the_value():
    # Header *names* are case-insensitive; workspace *ids* are not. Treating
    # "Org-Acme" as the same vault as "org-acme" would silently merge scopes.
    with pytest.raises(HTTPException) as excinfo:
        requested_workspace(_request(header="Org-Acme", query=ACME))
    assert excinfo.value.status_code == 403


# ── 1b. the header/query-only helper keeps its legacy precedence ─────────────
def test_scope_from_request_prefers_header_and_trims():
    assert workspace_scope_from_request(_request(header=f" {ACME} ", query=ZETA)) == ACME
    assert workspace_scope_from_request(_request(header="   ", query=f" {ZETA} ")) == ZETA
    assert workspace_scope_from_request(_request(header="", query="")) is None


def test_legacy_router_aliases_are_the_shared_implementation():
    # ``app_factory``/``namespace_runtime`` re-export these names as part of the
    # legacy ``server_app`` surface. If either router re-grows a local copy the
    # "stated once" guarantee is gone while every route test still passes.
    from latticeai.api import knowledge_graph as kg_module
    from latticeai.api import workspace as workspace_module

    assert kg_module._workspace_scope_from_request is workspace_scope_from_request
    assert workspace_module._workspace_scope_from_request is workspace_scope_from_request


# ── 2. resolve_workspace_scope: gating ───────────────────────────────────────
def test_no_workspace_service_passes_requested_value_through_ungated():
    scope = resolve_workspace_scope(_request(header=ACME), user="alice@test.local")
    assert scope == ACME  # not resolved onto "personal", not dropped


def test_no_workspace_service_still_enforces_selector_agreement():
    # The agreement check runs before the ungated short-circuit; an embedded
    # router without a workspace service is exactly where a silent "pick one"
    # would land a write in the wrong vault unnoticed.
    with pytest.raises(HTTPException) as excinfo:
        resolve_workspace_scope(
            _request(header=ACME),
            user="alice@test.local",
            body_workspace=ZETA,
        )
    assert excinfo.value.status_code == 403


def test_write_true_uses_the_write_gate():
    svc = _RecordingWorkspaceService()
    scope = resolve_workspace_scope(
        _request(header=ACME),
        user="alice@test.local",
        workspace_service=svc,
        write=True,
    )
    assert svc.calls == [("write", ACME, "alice@test.local")]
    assert scope == f"write::{ACME}"


def test_write_false_uses_the_read_gate():
    # A read routed through the write gate would 403 every viewer; a write
    # routed through the read gate would let a viewer write. The flag is the
    # whole distinction, so assert which resolver ran, not just the result.
    svc = _RecordingWorkspaceService()
    scope = resolve_workspace_scope(
        _request(header=ACME),
        user="viewer@test.local",
        workspace_service=svc,
        write=False,
    )
    assert svc.calls == [("read", ACME, "viewer@test.local")]
    assert scope == f"read::{ACME}"


def test_write_defaults_to_true():
    svc = _RecordingWorkspaceService()
    resolve_workspace_scope(
        _request(header=ACME), user="alice@test.local", workspace_service=svc
    )
    assert [mode for mode, _r, _u in svc.calls] == ["write"]


def test_permission_error_becomes_403_carrying_the_service_message():
    svc = _RecordingWorkspaceService(deny=ZETA)
    with pytest.raises(HTTPException) as excinfo:
        resolve_workspace_scope(
            _request(header=ZETA),
            user="stranger@test.local",
            workspace_service=svc,
        )
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == f"stranger@test.local cannot write workspace {ZETA}"


def test_permission_error_on_the_read_gate_is_also_403():
    svc = _RecordingWorkspaceService(deny=ZETA)
    with pytest.raises(HTTPException) as excinfo:
        resolve_workspace_scope(
            _request(query=ZETA),
            user="stranger@test.local",
            workspace_service=svc,
            write=False,
        )
    assert excinfo.value.status_code == 403
    assert "cannot read" in excinfo.value.detail


def test_blank_user_is_normalized_to_none_for_the_service():
    # "" is not an identity. Passing it through would make the service compare
    # membership against an empty string instead of the anonymous local user.
    svc = _RecordingWorkspaceService()
    resolve_workspace_scope(
        _request(header=ACME), user="", workspace_service=svc
    )
    assert svc.calls == [("write", ACME, None)]


# ── 2b. allow_unscoped_anonymous: the one deliberate exception ───────────────
def test_unscoped_anonymous_returns_none_without_consulting_the_service():
    svc = _RecordingWorkspaceService()
    scope = resolve_workspace_scope(
        _request(),
        user=None,
        workspace_service=svc,
        allow_unscoped_anonymous=True,
    )
    assert scope is None
    assert svc.calls == []  # legacy unscoped records stay unscoped


def test_unscoped_anonymous_is_off_by_default():
    # Without the flag the same anonymous, selector-free request is resolved
    # onto the active workspace. If it were the default, every legacy caller
    # would silently start writing unscoped rows.
    svc = _RecordingWorkspaceService()
    scope = resolve_workspace_scope(_request(), user=None, workspace_service=svc)
    assert svc.calls == [("write", None, None)]
    assert scope == "write::active"


def test_unscoped_anonymous_still_gates_a_named_workspace():
    svc = _RecordingWorkspaceService()
    scope = resolve_workspace_scope(
        _request(header=ACME),
        user=None,
        workspace_service=svc,
        allow_unscoped_anonymous=True,
    )
    assert svc.calls == [("write", ACME, None)]
    assert scope == f"write::{ACME}"


def test_unscoped_anonymous_does_not_apply_to_an_authenticated_caller():
    svc = _RecordingWorkspaceService()
    scope = resolve_workspace_scope(
        _request(),
        user="alice@test.local",
        workspace_service=svc,
        allow_unscoped_anonymous=True,
    )
    assert svc.calls == [("write", None, "alice@test.local")]
    assert scope == "write::active"


def test_unscoped_anonymous_never_bypasses_a_denied_workspace():
    svc = _RecordingWorkspaceService(deny=ZETA)
    with pytest.raises(HTTPException) as excinfo:
        resolve_workspace_scope(
            _request(header=ZETA),
            user=None,
            workspace_service=svc,
            allow_unscoped_anonymous=True,
        )
    assert excinfo.value.status_code == 403


def test_unscoped_anonymous_still_enforces_selector_agreement():
    svc = _RecordingWorkspaceService()
    with pytest.raises(HTTPException) as excinfo:
        resolve_workspace_scope(
            _request(header=ACME),
            user=None,
            workspace_service=svc,
            body_workspace=ZETA,
            allow_unscoped_anonymous=True,
        )
    assert excinfo.value.status_code == 403
    assert svc.calls == []


# ── 3. knowledge-graph stats/schema no longer count other workspaces ─────────
def _seeded_store(tmp_path) -> KnowledgeGraphStore:
    """Two organization workspaces plus one legacy-global row.

    Deliberately lopsided counts (2 / 3 / 1) so a leak shows up as a wrong
    number rather than an accidentally-equal one.
    """
    kg = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    with kg._connect() as conn:
        for index in range(2):
            kg._upsert_node(
                conn, f"n-acme-{index}", "Document", f"acme {index}", "acme body", {},
                workspace_id=ACME, visibility="workspace",
            )
        for index in range(3):
            kg._upsert_node(
                conn, f"n-zeta-{index}", "Document", f"zeta {index}", "zeta body", {},
                workspace_id=ZETA, visibility="workspace",
            )
        kg._upsert_node(
            conn, "n-legacy", "Document", "legacy", "machine-global", {},
        )
        kg._upsert_edge(conn, "n-acme-0", "n-acme-1", "mentions", 1.0, {})
        kg._upsert_edge(conn, "n-zeta-0", "n-zeta-1", "mentions", 1.0, {})
        kg._upsert_edge(conn, "n-zeta-1", "n-zeta-2", "mentions", 1.0, {})
        # Machine-local ingestion bookkeeping: no workspace column, so a
        # scoped read must report none of it rather than guess. Seeded
        # non-empty so "0 sources" is a decision, not an empty fixture.
        conn.execute(
            "INSERT INTO knowledge_sources(id, root_path, os_type, status, "
            "consent_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            ("src-1", "/tmp/docs", "darwin", "active", "{}", "now", "now"),
        )
        conn.execute(
            "INSERT INTO local_file_index(id, source_id, os_type, root_path, "
            "file_path, relative_path, file_name, extension, status, "
            "metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("lf-1", "src-1", "darwin", "/tmp/docs", "/tmp/docs/a.md", "a.md",
             "a.md", ".md", "indexed", "{}"),
        )
    return kg


def _kg_client(graph: Any, *, allowed=None, require_user=None) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_knowledge_graph_router(
            get_graph=lambda: graph,
            require_graph=lambda: None,
            require_user=require_user or (lambda _r: "alice@test.local"),
            static_dir=Path("/tmp"),
            allowed_workspaces_for=None if allowed is None else (lambda _u: set(allowed)),
        )
    )
    return TestClient(app)


def test_kg_stats_counts_only_the_callers_workspace(tmp_path):
    kg = _seeded_store(tmp_path)
    acme = _kg_client(kg, allowed=[ACME]).get("/knowledge-graph/stats").json()
    zeta = _kg_client(kg, allowed=[ZETA]).get("/knowledge-graph/stats").json()

    # 6 documents exist. Acme must count 2 and Zeta 3 — the whole-store total
    # is what leaked another tenant's volume off this endpoint.
    assert sum(acme["nodes"].values()) == 2
    assert sum(zeta["nodes"].values()) == 3
    assert sum(acme["edges"].values()) == 1
    assert sum(zeta["edges"].values()) == 2


def test_kg_stats_hides_legacy_global_rows_from_a_scoped_caller(tmp_path):
    kg = _seeded_store(tmp_path)
    payload = _kg_client(kg, allowed=[]).get("/knowledge-graph/stats").json()
    # A caller who is a member of nothing counts nothing — an empty allowed set
    # must not degrade into the unscoped whole-store query.
    assert payload["nodes"] == {}
    assert payload["edges"] == {}


def test_kg_stats_keeps_whole_store_counts_in_unscoped_mode(tmp_path):
    kg = _seeded_store(tmp_path)
    payload = _kg_client(kg, allowed=None).get("/knowledge-graph/stats").json()
    # Single-user / no-auth deployments get the same numbers they always did,
    # machine-local bookkeeping included.
    assert sum(payload["nodes"].values()) == 6
    assert sum(payload["edges"].values()) == 3
    assert payload["local_sources"] == 1
    assert payload["local_file_status"] == {"indexed": 1}


def test_kg_stats_reports_no_machine_local_bookkeeping_when_scoped(tmp_path):
    kg = _seeded_store(tmp_path)
    # The store holds one source and one indexed file; an unscoped caller sees
    # both (asserted above). A scoped caller sees neither, because these rows
    # carry no workspace and reporting them would answer a question the scope
    # cannot restrict.
    payload = _kg_client(kg, allowed=[ACME]).get("/knowledge-graph/stats").json()
    assert payload["local_sources"] == 0
    assert payload["local_file_status"] == {}


def test_kg_schema_v2_counts_are_workspace_scoped(tmp_path):
    kg = _seeded_store(tmp_path)
    acme = _kg_client(kg, allowed=[ACME]).get("/knowledge-graph/schema").json()
    zeta = _kg_client(kg, allowed=[ZETA]).get("/knowledge-graph/schema").json()

    assert acme["v2"]["nodes"] == 2
    assert zeta["v2"]["nodes"] == 3
    assert acme["v2"]["edges"] == 1
    # The response shape the SPA reads is unchanged.
    assert acme["v2_schema_available"] is True
    assert acme["legacy_schema_version"] == zeta["legacy_schema_version"]


def test_kg_stats_and_schema_still_require_a_user(tmp_path):
    # The migration deleted the explicit ``require_user(request)`` from both
    # handler bodies; authentication now happens inside ``_scoped``. If that
    # call were dropped these endpoints would be anonymous.
    def deny(_request):
        raise HTTPException(status_code=401, detail="auth required")

    client = _kg_client(_seeded_store(tmp_path), allowed=[ACME], require_user=deny)
    assert client.get("/knowledge-graph/stats").status_code == 401
    assert client.get("/knowledge-graph/schema").status_code == 401


class _LegacyStatsGraph:
    """A store predating scoped stats: ``stats()`` takes no scope argument."""

    def stats(self) -> Dict[str, Any]:
        return {
            "db_path": "/tmp/kg.sqlite",
            "schema_version": 3,
            "v2_schema_available": True,
            "nodes": {"Document": 42},
            "edges": {"mentions": 17},
            "local_sources": 9,
            "local_file_status": {"indexed": 9},
            "v2": {"nodes": 42, "edges": 17},
        }


def test_kg_stats_on_a_pre_scoping_store_empties_aggregates_instead_of_leaking():
    payload = _kg_client(_LegacyStatsGraph(), allowed=[ACME]).get(
        "/knowledge-graph/stats"
    ).json()
    # It cannot answer the scoped question, so it answers none of it rather
    # than handing back whole-store totals.
    assert payload["nodes"] == {}
    assert payload["edges"] == {}
    assert payload["local_sources"] == 0
    assert payload["local_file_status"] == {}
    # …while keeping the response shape the SPA reads.
    assert payload["schema_version"] == 3


def test_kg_stats_on_a_pre_scoping_store_is_untouched_when_unscoped():
    payload = _kg_client(_LegacyStatsGraph(), allowed=None).get(
        "/knowledge-graph/stats"
    ).json()
    assert payload["nodes"] == {"Document": 42}
    assert payload["local_sources"] == 9


# ── 4. end-to-end: a mismatched write is refused before it writes ────────────
def _browser_client(tmp_path, *, workspace_service=None):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    app = FastAPI()
    app.include_router(
        create_browser_router(
            pipeline=IngestionPipeline(store, enable_graph=True),
            require_user=lambda _r: "alice@test.local",
            workspace_service=workspace_service,
            fetch_url=lambda url: ("Example", "Readable body about Lattice AI."),
        )
    )
    return TestClient(app), store


def test_browser_read_url_rejects_a_header_body_workspace_mismatch(tmp_path):
    client, store = _browser_client(tmp_path)
    response = client.post(
        "/api/browser/read-url",
        headers={WORKSPACE_HEADER: ACME},
        json={"url": "https://example.com/post", "workspace_id": ZETA},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Workspace selectors must match."
    # The refusal has to happen before ingestion, not after.
    assert store.stats()["nodes"] == {}


def test_browser_read_url_rejects_a_query_body_workspace_mismatch(tmp_path):
    client, store = _browser_client(tmp_path)
    response = client.post(
        f"/api/browser/read-url?{WORKSPACE_PARAM}={ACME}",
        json={"url": "https://example.com/post", "workspace_id": ZETA},
    )
    assert response.status_code == 403
    assert store.stats()["nodes"] == {}


def test_browser_read_url_writes_to_the_agreed_workspace(tmp_path):
    # Positive control: the guard rejects disagreement, not every request that
    # names a workspace twice.
    client, store = _browser_client(tmp_path)
    response = client.post(
        "/api/browser/read-url",
        headers={WORKSPACE_HEADER: ACME},
        json={"url": "https://example.com/post", "workspace_id": ACME},
    )
    assert response.status_code == 200, response.text
    node_id = response.json()["node_id"]
    assert store.workspaces_of([node_id]) == {node_id: ACME}
