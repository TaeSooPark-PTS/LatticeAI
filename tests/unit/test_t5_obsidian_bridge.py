"""v11.1.0 Track 5 — the external Obsidian vault bridge.

A vault is somebody's real knowledge, so the bridge has to be boring in the
right ways: every note goes through the one ingestion gate (no second write
path), links become real edges only when both ends exist, a link that cannot
be resolved is *reported* rather than guessed at, and running twice changes
nothing. These tests assert those properties against a real
:class:`KnowledgeGraphStore` in ``tmp_path``, plus the parser edge cases that
only fakes can reach.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionPipeline, IngestionResult
from latticeai.api.local_files import create_local_files_router
from latticeai.api.permissions import create_permissions_router
from latticeai.services import obsidian_bridge as bridge_module
from latticeai.services.obsidian_bridge import (
    LINK_RELATION,
    TAG_RELATION,
    ObsidianVaultBridge,
    extract_link_targets,
    frontmatter_tags,
    parse_frontmatter,
)

USER = "vault-owner@example.com"
ADMIN_HEADERS = {"X-Test-Admin": "true"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _vault(root: Path) -> Path:
    """A small but representative vault: frontmatter, links, noise, a stub."""
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "alpha.md").write_text(
        "---\n"
        "title: Alpha decision\n"
        "tags: [decision, architecture]\n"
        "---\n\n"
        "Alpha links to [[beta]] and to [[notes/gamma|the third one]].\n"
        "It also links to [[missing note]] and again to [Beta](beta.md).\n"
        "External [site](https://example.com/x) is not a vault link.\n",
        encoding="utf-8",
    )
    (root / "notes" / "beta.md").write_text(
        "---\ntags:\n  - decision\n---\nBeta explains the storage choice in prose.\n",
        encoding="utf-8",
    )
    (root / "notes" / "gamma.md").write_text(
        "Gamma has no frontmatter at all, just body text about graphs.\n",
        encoding="utf-8",
    )
    (root / "notes" / "stub.md").write_text("---\ntags: draft\n---\n\n", encoding="utf-8")
    (root / "notes" / "readme.txt").write_text("not markdown", encoding="utf-8")
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "workspace.md").write_text("app config", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "dep.md").write_text("vendored", encoding="utf-8")
    return root


def _real_bridge(tmp_path: Path, **kwargs: Any):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    pipeline = IngestionPipeline(store)
    return ObsidianVaultBridge(pipeline=pipeline, knowledge_graph=store, **kwargs), store


class _FakePipeline:
    """Records ingests and hands back scripted results."""

    def __init__(self, *, results: Optional[List[IngestionResult]] = None,
                 is_available: bool = True) -> None:
        self._results = list(results or [])
        self._available = is_available
        self.items: List[Any] = []

    def available(self) -> bool:
        return self._available

    def ingest(self, item, *, user_email=None):
        self.items.append(item)
        if self._results:
            return self._results.pop(0)
        return IngestionResult(
            status="ok", source_type="obsidian", node_id=f"node-{len(self.items)}",
        )


class _RecordingGraph:
    def __init__(self, *, error: Optional[Exception] = None) -> None:
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def import_graph_data(self, data, *, mode="merge", dry_run=False):
        if self.error is not None:
            raise self.error
        self.calls.append(data)
        return {"imported": True, "index": {"status": "ready"}}


# ── frontmatter parsing ──────────────────────────────────────────────────────

def test_parse_frontmatter_reads_the_minimal_yaml_subset():
    data, body = parse_frontmatter(
        "---\n"
        "title: 'Quoted title'\n"
        "# a comment\n"
        "\n"
        "tags:\n"
        "  - one\n"
        "  - two\n"
        "aliases: [a, b]\n"
        "people: ann, bob\n"
        "empty:\n"
        "not a pair\n"
        "---\n"
        "Body starts here.\n"
    )
    assert data["title"] == "Quoted title"
    assert data["tags"] == ["one", "two"]
    assert data["aliases"] == ["a", "b"]
    assert data["people"] == ["ann", "bob"]
    assert data["empty"] == []
    assert body == "Body starts here."


def test_parse_frontmatter_leaves_a_note_without_a_block_untouched():
    assert parse_frontmatter("") == ({}, "")
    assert parse_frontmatter("no block here") == ({}, "no block here")
    # An opened-but-never-closed block is not frontmatter; the text is the note.
    unclosed = "---\ntags: x\nstill going"
    assert parse_frontmatter(unclosed) == ({}, unclosed)


def test_parse_frontmatter_ignores_list_items_it_cannot_attach():
    # A dash before any key, and a dash under a scalar key, are both dropped
    # rather than guessed into some other field.
    data, _ = parse_frontmatter("---\n- orphan\ntitle: T\n- also orphan\n...\nbody\n")
    assert data == {"title": "T"}


def test_frontmatter_tags_normalizes_and_dedupes():
    assert frontmatter_tags({"tags": ["#Decision", "decision", "", "Arch"]}) == [
        "Decision", "Arch",
    ]
    assert frontmatter_tags({"tag": "single"}) == ["single"]
    assert frontmatter_tags({"tags": None}) == []
    assert frontmatter_tags({}) == []


# ── link extraction ──────────────────────────────────────────────────────────

def test_extract_link_targets_handles_aliases_headings_and_externals():
    targets = extract_link_targets(
        "[[Plain]] [[Aliased|shown]] [[Deep#Heading]] [[Block^ref]] ![[Embedded]]\n"
        "[md](relative/note.md) [dup](Plain) [ext](https://example.com)\n"
        "[img](diagram.png) [anchor](#section) [proto](obsidian://open)\n"
        "[[]]\n"
    )
    assert targets == [
        "Plain", "Aliased", "Deep", "Block", "Embedded", "relative/note.md",
    ]


def test_extract_link_targets_on_an_empty_body():
    assert extract_link_targets("") == []


# ── scanning ─────────────────────────────────────────────────────────────────

def test_scan_walks_markdown_only_and_reports_what_it_skipped(tmp_path):
    bridge, _ = _real_bridge(tmp_path)
    report = bridge.scan(_vault(tmp_path / "vault"))

    assert report["status"] == "ok"
    names = sorted(note.relative_path for note in report["notes"])
    assert names == ["notes/alpha.md", "notes/beta.md", "notes/gamma.md"]
    # .txt is not counted as a note at all; a frontmatter-only stub is scanned
    # and then skipped as empty; hidden and vendored folders are never walked.
    assert report["scanned"] == 4
    assert report["skipped"]["empty"] == 1
    assert report["errors"] == []


def test_scan_refuses_a_path_that_is_not_a_vault(tmp_path):
    bridge, _ = _real_bridge(tmp_path)
    missing = bridge.scan(tmp_path / "nope")
    assert missing["status"] == "failed"
    assert "not a vault directory" in missing["detail"]

    invalid = bridge.scan(object())
    assert invalid["status"] == "failed"
    assert "invalid vault path" in invalid["detail"]


def test_scan_skips_hidden_notes_oversized_notes_and_unreadable_notes(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    (root / ".hidden.md").write_text("hidden note", encoding="utf-8")
    (root / "big.md").write_text("x" * 200, encoding="utf-8")
    (root / "fine.md").write_text("small enough note body", encoding="utf-8")
    (root / "broken.md").write_text("unreadable", encoding="utf-8")

    real_read = Path.read_text

    def _explode(self, *args, **kwargs):
        if self.name == "broken.md":
            raise OSError("permission denied")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _explode)
    bridge, _ = _real_bridge(tmp_path, max_file_bytes=100)
    report = bridge.scan(root)

    assert [note.relative_path for note in report["notes"]] == ["fine.md"]
    assert report["skipped"]["too_large"] == 1
    assert report["skipped"]["unreadable"] == 1
    assert report["errors"][0]["status"] == "unreadable"


def test_scan_truncates_at_the_file_cap_and_caps_the_error_report(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    for index in range(3):
        (root / f"n{index}.md").write_text(f"note {index} body", encoding="utf-8")
    bridge, _ = _real_bridge(tmp_path, max_files=2)
    report = bridge.scan(root)
    assert len(report["notes"]) == 2
    assert report["truncated"] is True
    assert report["scanned"] == 3

    monkeypatch.setattr(bridge_module, "ERROR_REPORT_CAP", 1)
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (_ for _ in ()).throw(OSError("nope")))
    capped, _ = _real_bridge(tmp_path / "second")
    capped_report = capped.scan(root)
    assert capped_report["skipped"]["unreadable"] == 3
    assert len(capped_report["errors"]) == 1


# ── link resolution ──────────────────────────────────────────────────────────

def test_resolve_links_prefers_paths_then_unique_basenames(tmp_path):
    root = tmp_path / "vault"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "dup.md").write_text("first dup body", encoding="utf-8")
    (root / "b" / "dup.md").write_text("second dup body", encoding="utf-8")
    (root / "unique.md").write_text("unique body", encoding="utf-8")
    (root / "hub.md").write_text(
        "Links: [[a/dup]] then [[dup]] then [[unique]] then [[hub]] then [[ghost]].",
        encoding="utf-8",
    )
    bridge, _ = _real_bridge(tmp_path)
    notes = bridge.scan(root)["notes"]
    resolved, unresolved = bridge.resolve_links(notes)

    assert resolved["hub.md"] == ["a/dup.md", "unique.md"]  # self-link dropped
    assert {(u["target"], u["reason"]) for u in unresolved} == {
        ("dup", "ambiguous"), ("ghost", "missing"),
    }


# ── sync ─────────────────────────────────────────────────────────────────────

def test_sync_ingests_notes_and_writes_link_and_tag_edges(tmp_path):
    bridge, store = _real_bridge(tmp_path)
    summary = bridge.sync(
        _vault(tmp_path / "vault"), owner=USER, workspace_id="ws-1", user_email=USER,
    )

    assert summary["status"] == "ok"
    assert summary["notes"] == 3
    assert summary["ingested"] == 3
    assert summary["duplicate"] == 0
    assert summary["failed"] == 0
    assert summary["tags"] == 2
    # alpha → beta (wikilink and the markdown link are one relation) and
    # alpha → gamma; the missing target is reported, never invented.
    assert summary["links"]["resolved"] == 2
    assert summary["links"]["written"] == 2
    assert summary["links"]["unresolved"] == [
        {"from": "notes/alpha.md", "target": "missing note", "reason": "missing"},
    ]
    assert summary["edges"]["status"] == "written"
    assert summary["edges"]["references"] == 2
    assert summary["edges"]["tags"] == 3
    assert summary["edges"]["topics"] == 2

    types = store.stats()["nodes"]
    assert types["Topic"] == 2
    relations = {
        (edge["from_node"], edge["type"])
        for edge in store.export_graph_data()["edges"]
    }
    assert any(kind == LINK_RELATION for _, kind in relations)
    assert any(kind == TAG_RELATION for _, kind in relations)


def test_sync_is_idempotent(tmp_path):
    vault = _vault(tmp_path / "vault")
    bridge, store = _real_bridge(tmp_path)
    bridge.sync(vault, workspace_id="ws-1")
    before = store.export_graph_data()["counts"]

    again = bridge.sync(vault, workspace_id="ws-1")

    assert again["ingested"] == 0
    assert again["duplicate"] == 3
    assert again["links"]["written"] == 2
    assert store.export_graph_data()["counts"] == before


def test_sync_dry_run_writes_nothing(tmp_path):
    bridge, store = _real_bridge(tmp_path)
    summary = bridge.sync(_vault(tmp_path / "vault"), dry_run=True)

    assert summary["status"] == "dry_run"
    assert summary["notes"] == 3
    assert summary["tags"] == 2
    assert summary["links"]["resolved"] == 2
    assert summary["ingested"] == 0
    assert sum(store.stats().get("nodes", {}).values()) == 0


def test_sync_reports_an_unavailable_pipeline_and_a_bad_vault(tmp_path):
    disabled = ObsidianVaultBridge(pipeline=_FakePipeline(is_available=False))
    assert disabled.sync(tmp_path)["status"] == "unavailable"
    assert ObsidianVaultBridge(pipeline=None).available() is False

    bridge, _ = _real_bridge(tmp_path)
    failed = bridge.sync(tmp_path / "no-such-vault")
    assert failed["status"] == "failed"
    assert failed["scanned"] == 0


def test_sync_records_per_note_failures_without_aborting(tmp_path, monkeypatch):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "good.md").write_text("good body", encoding="utf-8")
    (root / "bad.md").write_text("bad body", encoding="utf-8")
    pipeline = _FakePipeline(results=[
        IngestionResult(status="failed", source_type="obsidian", detail="disk full"),
        IngestionResult(status="ok", source_type="obsidian", node_id=None),
    ])
    bridge = ObsidianVaultBridge(pipeline=pipeline, knowledge_graph=_RecordingGraph())

    summary = bridge.sync(root)

    assert summary["status"] == "partial"
    assert summary["failed"] == 1
    assert summary["errors"][0] == {
        "path": "bad.md", "status": "failed", "detail": "disk full",
    }
    # The second note reported ok with no node id, so there is nothing to link.
    assert summary["edges"]["status"] == "none"

    monkeypatch.setattr(bridge_module, "ERROR_REPORT_CAP", 0)
    quiet = ObsidianVaultBridge(pipeline=_FakePipeline(results=[
        IngestionResult(status="failed", source_type="obsidian", detail="x"),
        IngestionResult(status="failed", source_type="obsidian", detail="y"),
    ]))
    capped = quiet.sync(root)
    assert capped["failed"] == 2
    assert capped["errors"] == []


def test_sync_skips_a_link_whose_target_failed_to_ingest(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("A links to [[b]].", encoding="utf-8")
    (root / "b.md").write_text("B body that will not land.", encoding="utf-8")
    graph = _RecordingGraph()
    bridge = ObsidianVaultBridge(
        pipeline=_FakePipeline(results=[
            IngestionResult(status="ok", source_type="obsidian", node_id="node-a"),
            IngestionResult(status="failed", source_type="obsidian", detail="boom"),
        ]),
        knowledge_graph=graph,
    )

    summary = bridge.sync(root)

    # The link resolved inside the vault, but its target never became a node,
    # so no edge is written and the count stays honest.
    assert summary["links"]["resolved"] == 1
    assert summary["links"]["written"] == 0
    assert summary["edges"]["status"] == "none"
    assert graph.calls == []


def test_sync_counts_duplicates_from_the_pipeline(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "one.md").write_text("body one", encoding="utf-8")
    pipeline = _FakePipeline(results=[
        IngestionResult(status="ok", source_type="obsidian", node_id="n1", duplicate=True),
    ])
    bridge = ObsidianVaultBridge(pipeline=pipeline, knowledge_graph=_RecordingGraph())
    summary = bridge.sync(root)
    assert summary["duplicate"] == 1
    assert summary["ingested"] == 0


def test_sync_without_a_graph_store_ingests_but_says_edges_were_skipped(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("---\ntags: t\n---\nA body", encoding="utf-8")
    bridge = ObsidianVaultBridge(pipeline=_FakePipeline(), knowledge_graph=None)

    summary = bridge.sync(root)

    assert summary["status"] == "ok"
    assert summary["edges"]["status"] == "skipped"
    assert "without link edges" in summary["edges"]["detail"]


def test_sync_reports_a_failed_edge_write_instead_of_crashing(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("---\ntags: t\n---\nA body", encoding="utf-8")
    bridge = ObsidianVaultBridge(
        pipeline=_FakePipeline(), knowledge_graph=_RecordingGraph(error=RuntimeError("locked")),
    )

    summary = bridge.sync(root)

    assert summary["status"] == "partial"
    assert summary["edges"]["status"] == "failed"
    assert "locked" in summary["edges"]["detail"]


def test_ingested_items_carry_the_vault_structure_as_metadata(tmp_path):
    pipeline = _FakePipeline()
    bridge = ObsidianVaultBridge(pipeline=pipeline, knowledge_graph=_RecordingGraph())
    bridge.sync(_vault(tmp_path / "vault"), workspace_id="ws-1")

    alpha = next(i for i in pipeline.items if i.metadata["relative_path"] == "notes/alpha.md")
    beta = next(i for i in pipeline.items if i.metadata["relative_path"] == "notes/beta.md")
    assert alpha.source_type == "obsidian"
    assert alpha.source_uri.endswith("notes/alpha.md")
    assert alpha.title == "Alpha decision"
    assert alpha.metadata["tags"] == ["decision", "architecture"]
    assert alpha.metadata["links"] == ["notes/beta.md", "notes/gamma.md"]
    assert alpha.metadata["backlinks"] == []
    assert beta.metadata["backlinks"] == ["notes/alpha.md"]


# ── route: POST /api/ingestion/obsidian ──────────────────────────────────────

def _require_admin(request: Request):
    if request.headers.get("X-Test-Admin") != "true":
        raise HTTPException(status_code=403, detail="admin required")
    return "admin@example.com"


def _client(tmp_path: Path, **kwargs: Any):
    config = SimpleNamespace(
        discord_permission_webhook="",
        discord_bot_token="",
        discord_permission_channel="",
        permission_monitor_secret="",
        port=4825,
    )
    permissions_router, gateway = create_permissions_router(
        config=config,
        data_dir=tmp_path / "perm",
        require_user=lambda request: USER,
        require_admin=_require_admin,
        get_current_user=lambda request: USER,
    )
    options: Dict[str, Any] = {
        "require_user": lambda request: USER,
        "tool_response": lambda fn, *args: {"status": "ok"},
        "permission_gateway": gateway,
        "knowledge_graph": None,
        "require_graph": lambda: None,
        "static_dir": tmp_path / "static",
        "local_kg_watcher": None,
    }
    options.update(kwargs)
    app = FastAPI()
    app.include_router(permissions_router)
    app.include_router(create_local_files_router(**options))
    return TestClient(app)


def test_obsidian_route_requires_the_local_read_approval(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    client = _client(
        tmp_path,
        ingestion_pipeline=IngestionPipeline(store),
        knowledge_graph=store,
    )
    vault = _vault(tmp_path / "vault")

    first = client.post("/api/ingestion/obsidian", json={"path": str(vault)})
    assert first.status_code == 200
    payload = first.json()
    assert payload["permission_required"] is True

    # Not approved yet: the token alone must not open the vault.
    unapproved = client.post("/api/ingestion/obsidian", json={
        "path": str(vault), "approved": True, "approval_token": payload["approval_token"],
    })
    assert unapproved.status_code == 403

    assert client.post(
        "/permissions/approve/" + payload["approval_token"], headers=ADMIN_HEADERS,
    ).status_code == 200

    synced = client.post("/api/ingestion/obsidian", json={
        "path": str(vault), "approved": True,
        "approval_token": payload["approval_token"], "workspace_id": "ws-1",
    })
    assert synced.status_code == 200
    body = synced.json()
    assert body["status"] == "ok"
    assert body["ingested"] == 3
    assert body["edges"]["references"] == 2


def test_obsidian_route_rejects_an_empty_path(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    client = _client(tmp_path, ingestion_pipeline=IngestionPipeline(store))
    response = client.post(
        "/api/ingestion/obsidian", json={"path": "  "}, headers={"Accept-Language": "en"},
    )
    assert response.status_code == 400
    assert "vault folder path is required" in response.json()["detail"]


def test_obsidian_route_supports_dry_run(tmp_path):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    client = _client(tmp_path, ingestion_pipeline=IngestionPipeline(store), knowledge_graph=store)
    vault = _vault(tmp_path / "vault")

    token = client.post("/api/ingestion/obsidian", json={"path": str(vault)}).json()["approval_token"]
    client.post("/permissions/approve/" + token, headers=ADMIN_HEADERS)

    body = client.post("/api/ingestion/obsidian", json={
        "path": str(vault), "approved": True, "approval_token": token, "dry_run": True,
    }).json()

    assert body["status"] == "dry_run"
    assert body["notes"] == 3
    assert sum(store.stats().get("nodes", {}).values()) == 0


def test_obsidian_route_reports_a_disabled_pipeline(tmp_path):
    client = _client(tmp_path, ingestion_pipeline=_FakePipeline(is_available=False))
    response = client.post("/api/ingestion/obsidian", json={"path": str(tmp_path)})
    assert response.status_code == 503


@pytest.mark.parametrize("raw,expected", [
    ("./Note.md", "note"),
    ("/Note.markdown", "note"),
    ("folder/Note", "folder/note"),
])
def test_index_key_normalizes_the_ways_obsidian_writes_a_target(raw, expected):
    assert bridge_module._index_key(raw) == expected
