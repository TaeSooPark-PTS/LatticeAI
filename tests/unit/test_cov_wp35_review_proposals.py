"""wp35: review-queue transitions and change-proposal staging/apply edges.

``ReviewQueueService`` takes its store, and ``ChangeProposalService`` takes the
queue plus a ``resolve_path`` callable — both are injected here so the tests
exercise the real policy layer against an in-memory store and ``tmp_path``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

import latticeai.services.change_proposals as cp
from latticeai.services.review_queue import (
    ReviewQueueService,
    enqueue_from_automation,
    review_queue_opted_in,
)


class FakeReviewStore:
    def __init__(self):
        self.items: Dict[str, Dict[str, Any]] = {}
        self.workflows: list = []
        self._sequence = 0

    def create_review_item(self, **fields):
        self._sequence += 1
        item_id = f"item-{self._sequence}"
        item = {"id": item_id, "status": "pending", "snoozed_until": None, **fields}
        self.items[item_id] = item
        return dict(item)

    def list_review_items(self, *, workspace_id=None, user_email=None, source=None):
        return [
            dict(item)
            for item in self.items.values()
            if source is None or item.get("source") == source
        ]

    def get_review_item(self, item_id, *, workspace_id=None):
        return dict(self.items[item_id])

    def update_review_item(self, item_id, *, workspace_id=None, **patch):
        self.items[item_id].update(patch)
        return dict(self.items[item_id])

    def create_workflow(self, **fields):
        self._sequence += 1
        workflow = {"id": f"wf-{self._sequence}", **fields}
        self.workflows.append(workflow)
        return dict(workflow)


def _queue():
    store = FakeReviewStore()
    return store, ReviewQueueService(store=store)


# ── review_queue ─────────────────────────────────────────────────────────────


def test_trigger_node_opt_in_is_read_from_its_config():
    opted_in = {
        "nodes": [{"id": "t", "type": "trigger", "config": {"review_queue": True}}]
    }
    opted_out = {
        "nodes": [
            {"id": "n", "type": "output", "config": {"review_queue": True}},
            {"id": "t", "type": "trigger", "config": {}},
        ]
    }

    assert review_queue_opted_in(opted_in) is True
    assert review_queue_opted_in(opted_out) is False


def test_dismiss_with_a_reason_clears_a_pending_snooze():
    store, queue = _queue()
    item = queue.create(title="suggestion")
    queue.snooze(item["id"], until="2030-01-01T00:00:00")

    dismissed = queue.dismiss(item["id"], reason="not useful right now")

    assert dismissed["status"] == "dismissed"
    assert dismissed["snoozed_until"] is None
    assert dismissed["provenance"]["dismiss_reason"] == "not useful right now"


def test_plain_dismiss_also_clears_the_snooze_timer():
    store, queue = _queue()
    item = queue.create(title="suggestion")
    queue.snooze(item["id"], until="2030-01-01T00:00:00")

    dismissed = queue.dismiss(item["id"])

    assert dismissed["status"] == "dismissed"
    assert dismissed["snoozed_until"] is None


def test_agent_followup_without_a_followup_is_not_promoted():
    store, queue = _queue()
    item = queue.create(title="", source="agent_followup", payload={})

    approved = queue.approve(item["id"])

    assert approved["status"] == "approved"
    assert store.workflows == []
    assert "promoted_workflow_id" not in approved["payload"]


def test_enqueue_from_automation_rejects_an_unknown_source():
    _, queue = _queue()

    with pytest.raises(ValueError, match="source must be one of"):
        enqueue_from_automation(queue, workflow={}, source="not-a-source", run_result=None)


@pytest.mark.parametrize(
    ("runner_result", "expected"),
    [
        (None, None),
        ({"run_id": "r-1"}, "r-1"),
        ({"agent_run_id": "r-3"}, "r-3"),
        ({"id": "r-5"}, "r-5"),
        ({"unrelated": "x"}, None),
    ],
)
def test_run_now_back_links_whatever_run_id_shape_the_runner_returns(
    runner_result, expected
):
    store, queue = _queue()
    item = queue.create(title="suggestion")

    updated = queue.run_now(item["id"], runner=lambda raw: runner_result)

    assert updated["status"] == "pending"
    if expected is None:
        assert "last_run_id" not in (updated.get("payload") or {})
    else:
        assert updated["payload"]["last_run_id"] == expected
        assert updated["provenance"]["run_id"] == expected


# ── change_proposals ─────────────────────────────────────────────────────────


class FakeProposalQueue:
    def __init__(self, *, create_error=None, dismiss_takes_reason=True):
        self.items: Dict[str, Dict[str, Any]] = {}
        self.create_error = create_error
        self.dismiss_takes_reason = dismiss_takes_reason
        self.dismiss_calls: list = []
        self._sequence = 0

    def create(self, **fields):
        if self.create_error is not None:
            raise self.create_error
        self._sequence += 1
        item_id = f"prop-{self._sequence}"
        item = {"id": item_id, "status": "pending", **fields}
        self.items[item_id] = item
        return dict(item)

    def get(self, item_id, *, workspace_id=None):
        return dict(self.items[item_id])

    def approve(self, item_id, *, workspace_id=None):
        self.items[item_id]["status"] = "approved"
        return dict(self.items[item_id])

    def dismiss(self, item_id, *, workspace_id=None, **kwargs):
        if kwargs and not self.dismiss_takes_reason:
            raise TypeError("dismiss() got an unexpected keyword argument 'reason'")
        self.dismiss_calls.append(kwargs)
        self.items[item_id]["status"] = "dismissed"
        return dict(self.items[item_id])


def _service(tmp_path: Path, queue: Optional[FakeProposalQueue] = None, resolve=None):
    queue = queue or FakeProposalQueue()
    return queue, cp.ChangeProposalService(
        review_queue=queue,
        resolve_path=resolve or (lambda path: tmp_path / path),
    )


def test_review_falls_through_when_the_governor_requires_no_proposal(
    tmp_path: Path, monkeypatch
):
    """The governor is the single source of classification.

    ``write_file`` can only be classified additive or mutation today, so the
    classifier is stubbed to prove the service defers to whatever it says
    rather than re-deriving the decision.
    """
    monkeypatch.setattr(
        cp,
        "classify_tool_call",
        lambda name, args, **kwargs: {
            "change_class": "exec",
            "proposal_required": False,
            "reason": "executes an action",
        },
    )
    _, service = _service(tmp_path)

    assert service.review("write_file", {"path": "a.txt", "content": "x"}) is None


def test_unresolvable_paths_are_treated_as_new_files():
    def boom(path):
        raise RuntimeError("path resolution offline")

    service = cp.ChangeProposalService(review_queue=FakeProposalQueue(), resolve_path=boom)

    verdict = service.review("write_file", {"path": "a.txt", "content": "x"})

    assert verdict["decision"] == "allow_additive"


def test_staging_failure_falls_back_to_the_normal_tool_path(tmp_path: Path):
    target = tmp_path / "notes.md"
    target.write_text("before", encoding="utf-8")
    queue = FakeProposalQueue(create_error=RuntimeError("queue offline"))
    _, service = _service(tmp_path, queue)

    assert service.review("write_file", {"path": "notes.md", "content": "after"}) is None


def test_snapshot_of_an_unreadable_target_reads_as_a_missing_base(tmp_path: Path):
    def boom(path):
        raise RuntimeError("resolver offline")

    queue = FakeProposalQueue()
    service = cp.ChangeProposalService(review_queue=queue, resolve_path=boom)

    item = service.propose_file_update(path="notes.md", new_content="after")

    assert item["payload"]["base_exists"] is False
    assert item["payload"]["base_sha256"] == ""
    assert item["payload"]["before_bytes"] == 0


def test_edit_staging_handles_replace_all_and_ambiguous_matches(tmp_path: Path):
    target = tmp_path / "notes.md"
    target.write_text("a\na\n", encoding="utf-8")
    _, service = _service(tmp_path)

    verdict = service.review(
        "edit_file", {"path": "notes.md", "old_string": "a", "new_string": "b", "replace_all": True}
    )
    assert verdict["decision"] == "proposed"
    assert verdict["proposal"]["payload"]["new_content"] == "b\nb\n"

    # Ambiguous (2 matches, no replace_all) cannot be staged deterministically.
    assert (
        service.review("edit_file", {"path": "notes.md", "old_string": "a", "new_string": "b"})
        is None
    )


def test_unknown_tools_have_no_staged_content(tmp_path: Path):
    _, service = _service(tmp_path)

    assert service._staged_content("run_command", {"command": "ls"}) is None


def test_approve_rejects_an_unknown_proposal_kind(tmp_path: Path):
    queue, service = _service(tmp_path)
    item = queue.create(
        title="cloud expansion",
        source="change_proposal",
        kind="kg_cloud_expansion",
        payload={"path": "notes.md"},
    )

    with pytest.raises(ValueError, match="unknown change proposal kind"):
        service.approve_and_apply(item["id"])


def test_atomic_write_cleans_up_and_re_raises_on_replace_failure(
    tmp_path: Path, monkeypatch
):
    target = tmp_path / "notes.md"
    target.write_text("before", encoding="utf-8")
    queue, service = _service(tmp_path)
    item = service.propose_file_update(path="notes.md", new_content="after")

    real_replace = os.replace
    real_unlink = os.unlink

    def failing_replace(src, dst, *args, **kwargs):
        if str(dst) == str(target):
            raise OSError(28, "no space left on device")
        return real_replace(src, dst, *args, **kwargs)

    def failing_unlink(path, *args, **kwargs):
        if str(path).startswith(str(tmp_path)):
            raise OSError(1, "operation not permitted")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)
    monkeypatch.setattr(os, "unlink", failing_unlink)

    with pytest.raises(OSError, match="no space left on device"):
        service.approve_and_apply(item["id"])

    # The reviewed file is untouched and the proposal is still open.
    assert target.read_text(encoding="utf-8") == "before"
    assert queue.items[item["id"]]["status"] == "pending"


def test_reject_falls_back_for_queues_without_reason_support(tmp_path: Path):
    queue = FakeProposalQueue(dismiss_takes_reason=False)
    _, service = _service(tmp_path, queue)
    item = service.propose_file_update(path="notes.md", new_content="after")

    result = service.reject(item["id"], reason="not now")

    assert result["applied"] is False
    assert result["reason"] == "not now"
    assert queue.dismiss_calls == [{}]
    assert queue.items[item["id"]]["status"] == "dismissed"
