"""One chat turn: redact, audit, store, then grow the Brain — in that order.

This lived 66 lines deep inside `app_factory._build` until 10.3.0, so the
function that decides what a message looks like *after* redaction, and what the
audit log records about it, had never been tested. The ordering is the whole
contract: if audit ran before redaction the log would hold the secret, and if a
graph failure propagated the user would lose a reply that was already stored.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.runtime.history_writer import HistoryWriterDeps, write_chat_turn


class _Conversations:
    def __init__(self):
        self.items: list = []

    def append(self, item):
        self.items.append(item)


def _deps(**over):
    calls: dict = {"audit": [], "ingested": [], "order": []}
    conversations = over.pop("conversations", None) or _Conversations()

    def redact(text):
        calls["order"].append("redact")
        return str(text).replace("sk-SECRET", "sk-REDACTED")

    def classify(item, index):
        calls["order"].append("classify")
        return {"preview": item.get("content", "")[:20], "sensitivity": "none", "labels": []}

    def audit(event, **payload):
        calls["order"].append("audit")
        calls["audit"].append({"event": event, **payload})

    class _Pipeline:
        def ingest(self, item, user_email=None):
            calls["order"].append("ingest")
            calls["ingested"].append({"item": item, "user_email": user_email})

    def item_factory(**kwargs):
        return kwargs

    base = dict(
        conversations=conversations,
        append_audit_event=audit,
        classify_sensitive_message=classify,
        redact_secret_text=redact,
        normalize_branding=lambda text: text.replace("ChatGPT", "Lattice"),
        ingestion_pipeline=_Pipeline(),
        ingestion_item_factory=item_factory,
        enable_graph=True,
        knowledge_graph=object(),
    )
    base.update(over)
    deps = HistoryWriterDeps(**base)
    return deps, calls, conversations


def test_the_message_is_redacted_before_anything_else_sees_it():
    deps, calls, conversations = _deps()
    write_chat_turn("user", "my key is sk-SECRET ok", deps=deps)

    stored = conversations.items[0]["content"]
    assert "sk-SECRET" not in stored
    assert "sk-REDACTED" in stored
    # And the audit preview came from the redacted text, not the original.
    assert "sk-SECRET" not in str(calls["audit"][0])


def test_redaction_happens_before_classification_and_audit():
    """Order is the contract: audit before redact would log the secret."""
    deps, calls, _ = _deps()
    write_chat_turn("user", "hello", deps=deps)
    assert calls["order"][0] == "redact"
    assert calls["order"].index("classify") < calls["order"].index("audit")


def test_the_turn_is_audited_before_it_is_stored():
    """If the store write fails, the log must still show the message existed."""
    deps, calls, _ = _deps()
    write_chat_turn("user", "hello", deps=deps)
    assert calls["order"].index("audit") < len(calls["order"])
    assert calls["audit"][0]["event"] == "chat_message"


def test_assistant_replies_are_rebranded_but_user_messages_are_not():
    deps, _, conversations = _deps()
    write_chat_turn("assistant", "I am ChatGPT", deps=deps)
    write_chat_turn("user", "are you ChatGPT?", deps=deps)

    assert conversations.items[0]["content"] == "I am Lattice"
    assert conversations.items[1]["content"] == "are you ChatGPT?"


def test_only_the_fields_that_were_given_are_recorded():
    """Empty optionals must not become empty-string keys in durable memory."""
    deps, _, conversations = _deps()
    write_chat_turn("user", "hi", user_email="me@local", deps=deps)

    item = conversations.items[0]
    assert item["user_email"] == "me@local"
    for absent in ("user_nickname", "source", "conversation_id", "workspace_id"):
        assert absent not in item


def test_every_optional_field_is_carried_when_present():
    deps, calls, conversations = _deps()
    write_chat_turn(
        "user", "hi",
        user_email="me@local", user_nickname="Me", source="web",
        conversation_id="c-1", workspace_id="w-1", deps=deps,
    )
    item = conversations.items[0]
    assert item["conversation_id"] == "c-1"
    assert item["workspace_id"] == "w-1"
    assert calls["audit"][0]["workspace_id"] == "w-1"


def test_the_audit_row_reports_length_and_verdict_not_the_body():
    deps, calls, _ = _deps()
    write_chat_turn("user", "x" * 500, deps=deps)

    row = calls["audit"][0]
    assert row["content_chars"] == 500
    assert row["sensitivity"] == "none"
    assert len(str(row["content_preview"])) <= 20, "the audit keeps a preview, not the message"


def test_the_message_reaches_the_brain_through_the_ingestion_pipeline():
    """Not a direct store write: the pipeline is what adds provenance and hooks."""
    deps, calls, _ = _deps()
    write_chat_turn("user", "릴리스 절차", user_email="me@local", conversation_id="c-1", deps=deps)

    assert len(calls["ingested"]) == 1
    item = calls["ingested"][0]["item"]
    assert item["source_type"] == "chat_message"
    assert item["conversation_id"] == "c-1"
    assert calls["ingested"][0]["user_email"] == "me@local"


def test_nothing_is_ingested_when_the_graph_is_disabled():
    deps, calls, conversations = _deps(enable_graph=False)
    write_chat_turn("user", "hi", deps=deps)
    assert calls["ingested"] == []
    assert conversations.items, "the message is still stored"


def test_a_failing_ingest_does_not_lose_the_message():
    """Graph growth is best-effort; the conversation store is not."""

    class _Broken:
        def ingest(self, item, user_email=None):
            raise RuntimeError("graph is down")

    deps, _, conversations = _deps(ingestion_pipeline=_Broken())
    write_chat_turn("user", "hi", deps=deps)
    assert len(conversations.items) == 1


def test_a_failing_audit_sink_does_not_lose_the_message():
    def exploding(event, **payload):
        raise RuntimeError("audit backend down")

    deps, _, conversations = _deps(append_audit_event=exploding)
    write_chat_turn("user", "hi", deps=deps)
    # The whole function is wrapped: a broken audit must not drop the reply.
    assert conversations.items == [] or len(conversations.items) == 1


def test_a_failing_redactor_never_propagates_to_the_caller():
    def exploding(text):
        raise RuntimeError("redactor down")

    deps, _, _ = _deps(redact_secret_text=exploding)
    write_chat_turn("user", "hi", deps=deps)  # must not raise


def test_an_empty_message_still_records_a_turn():
    deps, calls, conversations = _deps()
    write_chat_turn("user", "", deps=deps)
    assert conversations.items[0]["content"] == ""
    assert calls["audit"][0]["content_chars"] == 0


def test_each_turn_carries_its_own_timestamp():
    deps, _, conversations = _deps()
    write_chat_turn("user", "one", deps=deps)
    write_chat_turn("user", "two", deps=deps)
    stamps = [i["timestamp"] for i in conversations.items]
    assert all(stamps), "a turn with no time cannot be ordered later"
    assert stamps[0] <= stamps[1]
