"""v11.0.1 D4 — review-item ids stay unique inside a single second.

The id was ``review-`` plus a hash of ``[title, source, kind, user_email, now]``
and ``now`` has second resolution, so two identical drafts written in the same
second hashed to the same id. ``get_review_item`` / ``update_review_item`` both
return the *first* match, so the second draft was written to the state file and
then permanently unreachable — approving it approved the other one. A colliding
candidate is rehashed with a sequence suffix now.

Automation writes these drafts in bursts (one trigger run fanning out over a
digest), which is exactly the shape that produces same-second duplicates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from latticeai.core import workspace_review_items
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.core.workspace_os_utils import _json_hash


@pytest.fixture()
def frozen_second(monkeypatch: pytest.MonkeyPatch) -> str:
    """Every create in a test lands on the same wall-clock second."""
    stamp = "2026-08-09T12:00:00+09:00"
    monkeypatch.setattr(workspace_review_items, "_now", lambda: stamp)
    return stamp


def _store(tmp_path: Path) -> WorkspaceOSStore:
    target = tmp_path / "workspace"
    target.mkdir()
    return WorkspaceOSStore(target)


def test_identical_drafts_in_the_same_second_get_distinct_ids(tmp_path: Path, frozen_second: str):
    store = _store(tmp_path)

    items = [
        store.create_review_item(title="Daily digest", source="trigger", user_email="u@x.com")
        for _ in range(3)
    ]

    ids = [item["id"] for item in items]
    assert len(set(ids)) == 3
    assert all(len(item_id) == len("review-") + 16 for item_id in ids)


def test_every_draft_from_the_same_second_can_still_be_read_back(tmp_path: Path, frozen_second: str):
    store = _store(tmp_path)
    items = [
        store.create_review_item(title="Daily digest", source="trigger", user_email="u@x.com")
        for _ in range(3)
    ]

    for item in items:
        assert store.get_review_item(item["id"])["id"] == item["id"]

    stored = store.list_review_items()
    assert len(stored) == 3
    # Three rows, three reachable ids — not one id written three times.
    assert {row["id"] for row in stored} == {item["id"] for item in items}
    assert len({row["id"] for row in stored}) == 3


def test_updating_one_of_the_same_second_drafts_leaves_its_siblings_alone(
    tmp_path: Path, frozen_second: str,
):
    store = _store(tmp_path)
    first = store.create_review_item(title="Daily digest", source="trigger")
    second = store.create_review_item(title="Daily digest", source="trigger")

    store.update_review_item(second["id"], status="approved")

    assert store.get_review_item(second["id"])["status"] == "approved"
    assert store.get_review_item(first["id"])["status"] == "pending"


def test_the_uncontended_id_is_still_the_plain_hash(tmp_path: Path, frozen_second: str):
    """No collision means no suffix: existing ids keep the bytes they had."""
    store = _store(tmp_path)

    item = store.create_review_item(title="Only draft", source="trigger", user_email="u@x.com")

    expected = _json_hash(["Only draft", "trigger", "suggestion", "u@x.com", frozen_second])[:16]
    assert item["id"] == f"review-{expected}"


def test_a_draft_that_differs_only_by_workspace_does_not_reuse_a_sibling_id(
    tmp_path: Path, frozen_second: str,
):
    """workspace_id is not part of the hash, so scoped drafts collide too."""
    store = _store(tmp_path)

    personal = store.create_review_item(title="Same title", source="trigger")
    team = store.create_review_item(title="Same title", source="trigger", workspace_id="team")

    assert personal["id"] != team["id"]
    assert store.get_review_item(team["id"], workspace_id="team")["id"] == team["id"]
