"""State loading, identity migration, and pure helpers of the Workspace OS store.

Covers the recovery paths a real install hits — a corrupted SQLite row, a
hand-edited JSON state file, a legacy file that predates the workspace model —
plus the small pure helpers the store re-exports.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from latticeai.core import workspace_os as workspace_os_module
from latticeai.core.workspace_os import DEFAULT_WORKSPACE_ID, WorkspaceOSStore
from latticeai.core.workspace_os_state import (
    default_state,
    migrate_workspaces,
    new_workspace_record,
)
from latticeai.core.workspace_os_utils import (
    _snapshot_graph_import_payload,
    remove_skill_directory,
)


def _fresh_dir(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    target.mkdir()
    return target


def test_unreadable_sqlite_row_falls_back_to_the_json_state(tmp_path: Path):
    store = WorkspaceOSStore(_fresh_dir(tmp_path, "corrupt"))
    store.load_state()
    store.create_organization_workspace(name="Acme", owner_user_id="owner-1")

    conn = sqlite3.connect(store.sqlite_path)
    conn.execute("UPDATE workspace_os_state SET state_json = 'not-json' WHERE id = 'current'")
    conn.commit()
    conn.close()

    assert store._load_sqlite_state() is None

    recovered = store.load_state()

    # The JSON mirror still holds the organization, so nothing was lost.
    assert "org-Acme" in recovered["workspaces"]
    assert json.loads(store.state_path.read_text(encoding="utf-8"))["workspaces"]


def test_non_dict_json_state_is_ignored_in_favour_of_defaults(tmp_path: Path):
    data_dir = _fresh_dir(tmp_path, "listy")
    (data_dir / "workspace_os.json").write_text("[1, 2, 3]", encoding="utf-8")

    state = WorkspaceOSStore(data_dir).load_state()

    assert list(state["workspaces"]) == [DEFAULT_WORKSPACE_ID]
    assert state["onboarding"]["completed"] is False


def test_unparseable_json_state_is_ignored_in_favour_of_defaults(tmp_path: Path):
    data_dir = _fresh_dir(tmp_path, "garbage")
    (data_dir / "workspace_os.json").write_text("{oops", encoding="utf-8")

    state = WorkspaceOSStore(data_dir).load_state()

    assert list(state["workspaces"]) == [DEFAULT_WORKSPACE_ID]


def test_backup_failure_does_not_block_the_json_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = _fresh_dir(tmp_path, "nobackup")
    (data_dir / "workspace_os.json").write_text(
        json.dumps({"identity": "Imported Brain", "memories": [{"id": "m1"}]}),
        encoding="utf-8",
    )

    def refuse(*_args, **_kwargs):
        raise OSError("read-only medium")

    monkeypatch.setattr(workspace_os_module.shutil, "copy2", refuse)

    state = WorkspaceOSStore(data_dir).load_state()

    assert state["identity"] == "Imported Brain"
    assert state["memories"] == [{"id": "m1"}]
    assert not list(data_dir.glob("workspace_os.json.pre-sqlite.*.json"))


def test_identity_migration_is_a_no_op_without_a_mapping(tmp_path: Path):
    store = WorkspaceOSStore(_fresh_dir(tmp_path, "noop"))

    assert store.migrate_workspace_identities({}) == 0


def test_identity_migration_rewrites_owners_and_drops_duplicate_members(tmp_path: Path):
    store = WorkspaceOSStore(_fresh_dir(tmp_path, "identities"))
    state = store.load_state()
    record = store._new_workspace_record(
        workspace_id="org-dup",
        name="Dup",
        workspace_type="organization",
        owner_user_id="owner@example.com",
    )
    record["members"].append({"user_id": "dup@example.com", "role": "member"})
    record["members"].append({"user_id": "dup@example.com", "role": "viewer"})
    state["workspaces"]["org-dup"] = record
    store.save_state(state)

    changed = store.migrate_workspace_identities(
        {"owner@example.com": "uuid-owner", "dup@example.com": "uuid-dup", "unused@example.com": ""}
    )

    migrated = store.load_state()["workspaces"]["org-dup"]
    assert changed == 5  # owner field + three member rewrites + one duplicate drop
    assert migrated["owner_user_id"] == "uuid-owner"
    assert [m["user_id"] for m in migrated["members"]] == ["uuid-owner", "uuid-dup"]


def test_execution_events_ignore_unknown_types_and_survive_a_broken_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store = WorkspaceOSStore(_fresh_dir(tmp_path, "events"))
    before = len(store.load_state()["timeline"])

    store._emit_execution_event(
        area="agent", event_type="not_an_execution_event", payload={"x": 1}, workspace_id=None
    )
    assert len(store.load_state()["timeline"]) == before

    def refuse(*_args, **_kwargs):
        raise RuntimeError("timeline unavailable")

    monkeypatch.setattr(store, "record_timeline_event", refuse)
    store._emit_execution_event(
        area="agent", event_type="agent_started", payload={"run_id": "r1"}, workspace_id=None
    )


def test_active_workspace_falls_back_when_the_pointer_dangles(tmp_path: Path):
    store = WorkspaceOSStore(_fresh_dir(tmp_path, "dangling"))

    resolved = store._active_workspace_id({"active_workspace": "ghost", "workspaces": {"personal": {}}})

    assert resolved == DEFAULT_WORKSPACE_ID


def test_template_registry_key_only_prefixes_shared_workspaces():
    assert WorkspaceOSStore._template_registry_key("automation", "t1", DEFAULT_WORKSPACE_ID) == "automation:t1"
    assert WorkspaceOSStore._template_registry_key("automation", "t1", "org-acme") == "org-acme:automation:t1"


def test_new_workspace_record_rejects_unknown_types():
    with pytest.raises(ValueError, match="unknown workspace type"):
        new_workspace_record(
            workspace_id="x", name="X", workspace_type="team", owner_user_id=None
        )


def test_migrate_workspaces_repairs_a_non_dict_registry():
    state = migrate_workspaces({"workspaces": "not-a-mapping", "active_workspace": "ghost"})

    assert list(state["workspaces"]) == [DEFAULT_WORKSPACE_ID]
    assert state["active_workspace"] == DEFAULT_WORKSPACE_ID


def test_migrate_workspaces_skips_junk_entries_and_backfills_personal():
    state = migrate_workspaces(
        {
            "workspaces": {"org-a": {"name": "A", "type": "organization"}, "junk": "not-a-record"},
            "active_workspace": "junk",
        }
    )

    assert set(state["workspaces"]) == {"org-a", DEFAULT_WORKSPACE_ID}
    assert state["workspaces"][DEFAULT_WORKSPACE_ID]["type"] == "personal"
    assert state["active_workspace"] == DEFAULT_WORKSPACE_ID
    assert default_state()["workspaces"][DEFAULT_WORKSPACE_ID]["type"] == "personal"


def test_snapshot_graph_import_payload_normalizes_nodes_and_edges():
    payload = _snapshot_graph_import_payload(
        {
            "nodes": [
                {"label": "no id"},
                {"id": "n1", "metadata": {"kept": True}, "raw": {"body": "text"}},
                {"id": "n2", "type": "Decision", "title": "T", "metadata": "not-a-dict"},
            ],
            "edges": [
                {"from": "n1"},
                {"from_node": "n1", "to_node": "n2", "type": "leads_to", "weight": 0.5},
                {"source": "n2", "target": "n1"},
            ],
        },
        workspace_id="org-acme",
    )

    assert payload["counts"] == {"nodes": 2, "edges": 2}
    assert payload["header"]["workspace_id"] == "org-acme"
    first = json.loads(payload["nodes"][0]["metadata_json"])
    assert first == {"kept": True, "workspace_id": "org-acme"}
    assert payload["nodes"][1]["title"] == "T"
    assert payload["edges"][0]["type"] == "leads_to"
    assert payload["edges"][1]["type"] == "related_to"


def test_remove_skill_directory_deletes_only_paths_inside_the_skills_root(tmp_path: Path):
    skills_dir = _fresh_dir(tmp_path, "skills")
    outside = _fresh_dir(tmp_path, "outside")
    (skills_dir / "demo").mkdir()
    (skills_dir / "demo" / "SKILL.md").write_text("description: demo", encoding="utf-8")
    (skills_dir / "afile").write_text("not a skill", encoding="utf-8")
    os.symlink(outside, skills_dir / "escape")

    with pytest.raises(ValueError, match="invalid skill path"):
        remove_skill_directory(skills_dir, "escape")
    with pytest.raises(FileNotFoundError):
        remove_skill_directory(skills_dir, "ghost")
    with pytest.raises(FileNotFoundError):
        remove_skill_directory(skills_dir, "afile")

    removed = remove_skill_directory(skills_dir, "demo")

    assert removed["status"] == "ok"
    assert removed["skill"] == "demo"
    assert not (skills_dir / "demo").exists()
    assert outside.exists()
