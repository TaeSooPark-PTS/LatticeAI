"""wpb03: Workspace OS state paths that run on an already-migrated install.

Three of these only happen on a machine that has been upgraded once already —
a JSON state file whose pre-SQLite backup was taken by an earlier boot, and an
identity migration where most memberships are already UUIDs or were written
without one.  The other two are the plainest calls the memory API has: a
browse-everything search with no query, and a snapshot taken for a whole
workspace rather than for one person.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from latticeai.core.workspace_os import WorkspaceOSStore

# ── one-time JSON → SQLite import ───────────────────────────────────────────


def test_a_second_import_does_not_take_a_second_pre_sqlite_backup(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    state_path = data_dir / "workspace_os.json"
    state_path.write_text(
        json.dumps({"memories": [{"id": "memory-legacy", "content": "이전 기록"}]}),
        encoding="utf-8",
    )
    # An earlier boot already archived the pre-SQLite copy.
    existing_backup = data_dir / "workspace_os.json.pre-sqlite.2026-01-01T00-00-00Z.json"
    existing_backup.write_text("{}", encoding="utf-8")

    store = WorkspaceOSStore(data_dir)
    state = store.load_state()

    assert [m["id"] for m in state["memories"]] == ["memory-legacy"]
    backups = sorted(p.name for p in data_dir.glob("workspace_os.json.pre-sqlite.*.json"))
    assert backups == [existing_backup.name], "the archive is taken once, not per boot"


# ── identity migration ──────────────────────────────────────────────────────


def _seed_workspace(store: WorkspaceOSStore, members: Any) -> Dict[str, Any]:
    state = store.load_state()
    state["workspaces"]["ws-1"] = {
        "id": "ws-1",
        "name": "Team",
        "owner_user_id": "owner@example.com",
        "members": members,
    }
    store.save_state(state)
    return state


def test_only_the_memberships_that_still_use_an_email_are_rewritten(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    _seed_workspace(store, [
        {"user_id": "owner@example.com", "role": "owner"},
        {"user_id": "user:already-a-uuid", "role": "editor"},
        {"role": "viewer"},
    ])

    changed = store.migrate_workspace_identities({"owner@example.com": "user:1111"})

    workspace = store.load_state()["workspaces"]["ws-1"]
    assert workspace["owner_user_id"] == "user:1111"
    assert [m.get("user_id") for m in workspace["members"]] == [
        "user:1111",
        "user:already-a-uuid",
        None,
    ]
    # Owner field + one membership; the UUID member and the id-less row are untouched.
    assert changed == 2
    assert "updated_at" not in workspace["members"][1]


# ── memory reads ────────────────────────────────────────────────────────────


def test_an_empty_search_query_returns_the_whole_scoped_memory_list(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    store.upsert_memory(kind="workspace", content="분기 회고 메모", user_email="owner@example.com")
    store.upsert_memory(kind="preferences", content="다크 모드 선호", user_email="owner@example.com")

    result = store.search_memories("")

    assert result["query"] == ""
    contents = {item["content"] for item in result["memories"]}
    assert contents == {"분기 회고 메모", "다크 모드 선호"}


def test_a_workspace_wide_snapshot_keeps_every_members_memory(tmp_path: Path):
    store = WorkspaceOSStore(tmp_path)
    store.upsert_memory(kind="workspace", content="내 메모", user_email="owner@example.com")
    store.upsert_memory(kind="workspace", content="동료 메모", user_email="mate@example.com")

    snapshot = store.create_memory_snapshot(label="팀 스냅샷", reason="분기 마감")

    assert snapshot["user_email"] is None
    assert snapshot["memory_count"] == 2
    assert {m["content"] for m in snapshot["memories"]} == {"내 메모", "동료 메모"}
    assert snapshot["label"] == "팀 스냅샷"
