"""Hermetic branch arcs that a clean Linux runner reported as never taken.

Same class of gap as :mod:`tests.unit.test_cov_wp37_linux_parity`, one layer
down: with ``branch = true`` a container run of python:3.14 found three arcs
that only ever flipped on the development Mac, and only because real files
under the developer's home (the ``~/.ltcai-brain`` vault, a lived-in
workspace state db) made the "already there, do nothing" side of a guard
reachable. Each test below drives the missed direction through an explicit
seam — a monkeypatched vault dir, a fake workflow store, a store rooted at
``tmp_path`` — so the second run of an idempotent operation is a real
assertion instead of an accident of the machine.
"""

from types import SimpleNamespace

from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services import p_reinforce as p_reinforce_mod
from latticeai.services.p_reinforce import PReinforceGardener
from latticeai.services.triggers import TriggerService


# ── latticeai/services/p_reinforce.py 48→exit — INDEX.md already exists ──────
def test_second_gardener_keeps_the_existing_vault_index(tmp_path, monkeypatch):
    monkeypatch.setattr(p_reinforce_mod, "BRAIN_DIR", tmp_path)
    index_path = tmp_path / "INDEX.md"

    PReinforceGardener()

    assert index_path.exists()  # first construction seeds the vault
    hand_edited = "# my own index\n\nnotes I do not want clobbered\n"
    index_path.write_text(hand_edited, encoding="utf-8")

    PReinforceGardener()

    # The guard's false side: an existing index is left exactly as the user
    # left it, and the folder structure is still ensured around it.
    assert index_path.read_text(encoding="utf-8") == hand_edited
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_dir()) == sorted(
        p_reinforce_mod.STRUCTURE
    )


# ── latticeai/services/triggers.py 112→105 — enabled but unscheduled kind ────
def test_enabled_manual_trigger_node_is_skipped_by_the_scanner(tmp_path):
    workflow = {
        "id": "wf-mixed",
        "name": "manual next to interval",
        "nodes": [
            {"id": "trig-manual", "type": "trigger",
             "config": {"trigger": "manual", "enabled": True}},
            {"id": "trig-interval", "type": "trigger",
             "config": {"trigger": "interval", "enabled": True, "interval_seconds": 60}},
        ],
    }
    store = SimpleNamespace(load_state=lambda: {"workflows": [workflow]})
    service = TriggerService(
        store=store,
        run_workflow=lambda _wf_id, _payload: {"status": "ok"},
        data_dir=tmp_path,
        tz_name="UTC",
    )

    triggered = service._triggered_workflows()

    # "manual" is enabled, so it survives the disabled check and is dropped by
    # the kind check instead — the scanner loops on to the next node.
    assert [item["node"]["id"] for item in triggered] == ["trig-interval"]
    assert [item["kind"] for item in triggered] == ["interval"]
    assert [entry["workflow_id"] for entry in service.describe()["armed"]] == ["wf-mixed"]


# ── latticeai/core/workspace_os.py 235→239 — migration with nothing to do ────
def test_repeated_identity_migration_reports_zero_and_saves_nothing(tmp_path):
    store = WorkspaceOSStore(tmp_path / "workspace-os")
    state = store.load_state()
    state["workspaces"]["org-repeat"] = store._new_workspace_record(
        workspace_id="org-repeat",
        name="Repeat",
        workspace_type="organization",
        owner_user_id="owner@example.com",
    )
    store.save_state(state)
    mapping = {"owner@example.com": "uuid-owner"}

    assert store.migrate_workspace_identities(mapping) == 2  # owner field + member row
    settled = store.load_state()

    assert store.migrate_workspace_identities(mapping) == 0

    # The no-op exit: nothing re-saved, no second migration event recorded.
    after = store.load_state()
    assert after["workspaces"]["org-repeat"]["owner_user_id"] == "uuid-owner"
    assert after["updated_at"] == settled["updated_at"]
    assert [
        event for event in after["timeline"] if event["event_type"] == "identity_uuid_migrated"
    ] == [
        event for event in settled["timeline"] if event["event_type"] == "identity_uuid_migrated"
    ]
