"""Shared fixtures for the Track 2 (temporal + synthesis) suites.

One real ``KnowledgeGraphStore`` builder and one recording review queue, so the
other ``test_t2_*`` modules exercise the real SQLite schema and the real
proposal contract instead of each inventing its own stand-in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from lattice_brain.graph.store import KnowledgeGraphStore


def make_store(tmp_path: Path, name: str = "kg") -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / f"{name}.sqlite", tmp_path / f"{name}-blobs")


def seed(store: KnowledgeGraphStore, rows) -> None:
    """Insert ``(id, type, title, summary)`` rows through the real write door."""
    with store._connect() as conn:
        for node_id, node_type, title, summary in rows:
            store._upsert_node(conn, node_id, node_type, title, summary)


def link(store: KnowledgeGraphStore, source: str, target: str, edge_type: str = "RELATED_TO") -> None:
    with store._connect() as conn:
        store._upsert_edge(conn, source, target, edge_type)


class RecordingReviewQueue:
    """In-memory stand-in with ``ReviewQueueService``'s proposal surface.

    Records every ``create``/``approve`` call so a test can assert that a
    graph change was preceded by an approval — the whole point of the
    proposal-first contract.
    """

    def __init__(self, *, fail_list: bool = False) -> None:
        self.items: Dict[str, Dict[str, Any]] = {}
        self.created: List[Dict[str, Any]] = []
        self.approved: List[str] = []
        self._seq = 0
        self._fail_list = fail_list

    def create(self, **fields: Any) -> Dict[str, Any]:
        self._seq += 1
        item = {
            "id": f"review-{self._seq}",
            "status": "pending",
            "effective_status": "pending",
            **fields,
        }
        self.items[item["id"]] = item
        self.created.append(item)
        return item

    def list(self, **_kwargs: Any) -> Dict[str, Any]:
        if self._fail_list:
            raise RuntimeError("inbox unavailable")
        return {"items": list(self.items.values())}

    def get(self, item_id: str, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        return self.items[item_id]

    def approve(self, item_id: str, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        item = self.items[item_id]
        item["status"] = "approved"
        item["effective_status"] = "approved"
        self.approved.append(item_id)
        return item


CONTRADICTION_ROWS = [
    ("n-old", "Concept", "coffee ritual", "I like coffee before the design review"),
    ("n-new", "Concept", "coffee ritual", "I do not like coffee before the design review"),
]


def test_support_helpers_build_a_real_store(tmp_path):
    store = make_store(tmp_path)
    seed(store, CONTRADICTION_ROWS)
    link(store, "n-old", "n-new")
    assert {node["id"] for node in store.graph(50)["nodes"]} == {"n-old", "n-new"}
    queue = RecordingReviewQueue()
    queue.create(title="t", payload={})
    assert queue.list()["items"][0]["title"] == "t"
