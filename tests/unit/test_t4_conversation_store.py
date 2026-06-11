"""T4.2: durable conversation store — the 50-message cap is dead.

Pre-upgrade history must survive the cutover (idempotent legacy import),
the item shape must match the legacy chat_history.json entries exactly,
and the clear semantics (including the legacy-previous-history bucket and
started_at handling) must mirror the JSON implementation.
"""

import json

from latticeai.brain.conversations import ConversationStore


def _store(tmp_path):
    return ConversationStore(tmp_path / "kg.sqlite")


def _item(i, conv="conv-1", role="user", email="a@b.c"):
    return {
        "role": role,
        "content": f"message {i}",
        "timestamp": f"2026-06-11T10:00:{i:02d}",
        "user_email": email,
        "conversation_id": conv,
    }


def test_history_is_unbounded_past_fifty(tmp_path):
    store = _store(tmp_path)
    for i in range(80):
        store.append({**_item(i % 60), "content": f"unique {i}", "timestamp": f"2026-06-11T10:{i // 60}:{i % 60:02d}"})
    assert store.count() == 80, "the 50-message cap must be gone"
    history = store.history()
    assert len(history) == 80
    assert history[0]["content"] == "unique 0"
    assert history[-1]["content"] == "unique 79"


def test_item_shape_matches_legacy_contract(tmp_path):
    store = _store(tmp_path)
    original = {
        "role": "assistant",
        "content": "hello",
        "timestamp": "2026-06-11T10:00:00",
        "user_email": "a@b.c",
        "user_nickname": "A",
        "source": "web",
        "conversation_id": "c1",
        "image_attached": True,  # arbitrary extra key must round-trip
    }
    store.append(original)
    assert store.history() == [original]


def test_legacy_import_is_idempotent_and_preserves_messages(tmp_path):
    legacy = tmp_path / "chat_history.json"
    items = [_item(i) for i in range(5)]
    legacy.write_text(json.dumps(items, ensure_ascii=False))
    store = _store(tmp_path)
    assert store.import_legacy_json(legacy) == 5
    assert store.import_legacy_json(legacy) == 0, "re-import must not duplicate"
    assert store.count() == 5
    assert [m["content"] for m in store.history()] == [m["content"] for m in items], (
        "pre-upgrade messages must be visible post-cutover"
    )


def test_clear_all_keep_last(tmp_path):
    store = _store(tmp_path)
    for i in range(10):
        store.append(_item(i))
    result = store.clear_all(keep_last=3)
    assert result == {"status": "cleared", "removed": 7, "kept": 3}
    assert [m["content"] for m in store.history()] == ["message 7", "message 8", "message 9"]


def test_clear_conversation_and_legacy_bucket(tmp_path):
    store = _store(tmp_path)
    store.append(_item(1, conv="keep"))
    store.append(_item(2, conv="drop"))
    unattributed_old = {"role": "user", "content": "old", "timestamp": "2026-06-10T09:00:00"}
    unattributed_new = {"role": "user", "content": "new", "timestamp": "2026-06-12T09:00:00"}
    store.append(unattributed_old)
    store.append(unattributed_new)

    # started_at also sweeps unattributed messages from that point on (legacy parity).
    result = store.clear_conversation("drop", started_at="2026-06-12T00:00:00")
    assert result["removed"] == 2
    contents = [m["content"] for m in store.history()]
    assert "message 1" in contents and "old" in contents
    assert "message 2" not in contents and "new" not in contents

    # The legacy bucket removes the remaining unattributed message.
    result = store.clear_conversation("legacy-previous-history")
    assert result["removed"] == 1
    assert [m["content"] for m in store.history()] == ["message 1"]


def test_per_conversation_history(tmp_path):
    store = _store(tmp_path)
    store.append(_item(1, conv="a"))
    store.append(_item(2, conv="b"))
    assert [m["content"] for m in store.history(conversation_id="a")] == ["message 1"]


def test_backup_restore_round_trip_carries_conversations(tmp_path):
    """Conversations share the KG database file, so the existing
    kg_portability backup/restore covers them — prove it end-to-end."""
    from knowledge_graph import KnowledgeGraphStore
    from latticeai.services.kg_portability import KGPortabilityService

    db = tmp_path / "knowledge_graph.sqlite"
    kg = KnowledgeGraphStore(db, tmp_path / "blobs")
    conv = ConversationStore(db)
    conv.append(_item(1))
    conv.append(_item(2))

    portability = KGPortabilityService(knowledge_graph=kg, data_dir=tmp_path, enable_graph=True)
    backup = portability.backup()
    assert backup["path"]

    conv.clear_all()
    assert conv.count() == 0

    portability.restore(backup["path"])
    restored = ConversationStore(db)
    assert restored.count() == 2, "restore must bring conversations back"
    assert [m["content"] for m in restored.history()] == ["message 1", "message 2"]
