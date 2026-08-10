"""wpb03: identity migration and agent-registry edits that change one thing.

Two "nothing to do here" directions were never executed:

* ``migrate_users`` merging two spellings of the same address when *neither*
  copy carries an API-key map — the common case, since keys live in the OS
  keyring;
* ``migrate_knowledge_graph_identity`` running against a Brain database created
  before the v2 tables existed, where every table probe misses;
* ``AgentRegistry`` reading a registry file that holds a JSON array, and
  ``update_config`` called without an ``enabled`` flag (the Admin screen sends
  config-only edits) for both a custom and a built-in agent.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from latticeai.core.agent_registry import AgentRegistry
from latticeai.core.users import migrate_knowledge_graph_identity, migrate_users

# ── users.migrate_users ─────────────────────────────────────────────────────


def test_two_spellings_of_one_address_merge_without_inventing_an_api_key_map():
    users = {
        "Owner@Example.com": {"nickname": "Owner (old casing)", "role": "admin"},
        "owner@example.com": {"nickname": "Owner", "disabled": False},
    }

    migrated, email_to_id, changed = migrate_users(users)

    assert changed is True
    assert list(migrated) == ["owner@example.com"]
    merged = migrated["owner@example.com"]
    assert merged["nickname"] == "Owner", "the later record wins on conflict"
    assert merged["role"] == "admin", "fields only the earlier record had survive"
    assert "api_keys" not in merged, "no empty key map is fabricated"
    assert email_to_id["owner@example.com"] == merged["id"]


# ── users.migrate_knowledge_graph_identity ──────────────────────────────────


def test_a_pre_v2_brain_database_is_left_untouched(tmp_path: Path):
    db_path = tmp_path / "knowledge_graph.sqlite"
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("CREATE TABLE legacy_nodes (id TEXT PRIMARY KEY, owner TEXT)")
        conn.execute("INSERT INTO legacy_nodes VALUES ('n1', 'owner@example.com')")

    changed = migrate_knowledge_graph_identity(
        db_path,
        {"owner@example.com": "user:1111", "other@example.com": "user:2222"},
    )

    assert changed == 0
    with closing(sqlite3.connect(db_path)) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        owners = [row[0] for row in conn.execute("SELECT owner FROM legacy_nodes")]
    assert tables == {"legacy_nodes"}, "no migration marker table is created"
    assert owners == ["owner@example.com"], "legacy rows are not rewritten"


# ── agent_registry ──────────────────────────────────────────────────────────


def test_a_registry_file_holding_a_json_array_falls_back_to_an_empty_registry(tmp_path: Path):
    path = tmp_path / "agents.json"
    path.write_text(json.dumps([{"id": "agent:custom:ghost"}]), encoding="utf-8")

    registry = AgentRegistry(path)

    assert [a["id"] for a in registry.all() if a["source"] == "user"] == []
    assert registry.get("agent:custom:ghost") is None
    # The built-in roles are still projected, so the surface never goes blank.
    assert any(a["source"] == "builtin" for a in registry.all())


def test_a_config_only_edit_on_a_custom_agent_keeps_it_enabled(tmp_path: Path):
    registry = AgentRegistry(tmp_path / "agents.json")
    registry.register(name="First Helper", capabilities=["summarize"])
    second = registry.register(name="Second Helper", capabilities=["draft"])

    updated = registry.update_config(second["id"], {"temperature": 0.2})

    assert updated is not None
    assert updated["config"] == {"temperature": 0.2}
    assert updated["enabled"] is True, "an unspecified flag is not treated as False"
    # The first custom agent was walked past, not edited.
    first = registry.get("agent:custom:first-helper")
    assert first is not None
    assert first["config"] == {}
    # The change survives a reload from disk.
    assert AgentRegistry(tmp_path / "agents.json").get(second["id"])["config"] == {
        "temperature": 0.2
    }


def test_a_config_only_edit_on_a_builtin_role_records_no_enabled_override(tmp_path: Path):
    path = tmp_path / "agents.json"
    registry = AgentRegistry(path)
    builtin = next(a for a in registry.all() if a["source"] == "builtin")

    updated = registry.update_config(builtin["id"], {"max_steps": 3})

    assert updated is not None
    assert updated["config"] == {"max_steps": 3}
    assert updated["enabled"] is True
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["config_overrides"][builtin["id"]] == {"config": {"max_steps": 3}}
