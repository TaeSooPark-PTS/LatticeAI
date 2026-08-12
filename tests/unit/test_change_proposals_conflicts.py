"""Change proposal conflict-detection tests (P0 data-loss fix).

v9.6.0 staged proposals but ``approve_and_apply`` wrote the staged content
without checking whether the target file had changed since staging — a user
edit made between "propose" and "approve" was silently destroyed. These
tests pin the fix:

* propose stores a base snapshot (``base_exists`` + ``base_sha256``);
* approve re-hashes the disk state and refuses to apply on drift
  (:class:`ProposalConflictError` → HTTP 409 with a rebase hint);
* writes are atomic (same-dir temp + ``os.replace``);
* duplicate/concurrent approvals are serialized — exactly one applies.
"""

import hashlib
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.review_queue import create_review_queue_router
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.change_proposals import (
    ChangeProposalService,
    ProposalConflictError,
)
from latticeai.services.review_queue import ReviewQueueService


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeReviewQueue:
    def __init__(self):
        self.items = {}
        self.counter = 0

    def create(self, **kwargs):
        self.counter += 1
        item = {"id": f"rp-{self.counter}", "status": "pending", **kwargs}
        self.items[item["id"]] = item
        return item

    def get(self, item_id, *, workspace_id=None):
        if item_id not in self.items:
            raise FileNotFoundError(item_id)
        return self.items[item_id]

    def approve(self, item_id, *, workspace_id=None):
        self.items[item_id]["status"] = "approved"
        return self.items[item_id]

    def dismiss(self, item_id, *, workspace_id=None):
        self.items[item_id]["status"] = "dismissed"
        return self.items[item_id]


def _service(tmp_path):
    queue = FakeReviewQueue()

    def resolve(path=""):
        candidate = (tmp_path / path).resolve()
        assert str(candidate).startswith(str(tmp_path))
        return candidate

    return ChangeProposalService(review_queue=queue, resolve_path=resolve), queue


def _stage_delete(queue, path: str, base_content: str):
    """Stage a ``file_delete`` proposal the way a client does.

    There is no Python-side factory for delete proposals: they are created
    through ``POST /automation/reviews`` (the frontend's proposal-rebase flow
    is the live producer), so the tests stage them at that same seam — base
    snapshot included, since the conflict check is what is under test.
    """
    return queue.create(
        title=f"파일 삭제 제안: {path}",
        summary="",
        source="change_proposal",
        kind="file_delete",
        payload={
            "path": path,
            "tier": "large",
            "base_exists": True,
            "base_sha256": _sha(base_content),
        },
    )


# ── base snapshot stored at propose time ─────────────────────────────────

def test_update_proposal_stores_base_snapshot(tmp_path):
    (tmp_path / "note.txt").write_text("original\n", encoding="utf-8")
    service, _ = _service(tmp_path)
    item = service.propose_file_update(path="note.txt", new_content="changed\n")
    payload = item["payload"]
    assert payload["base_exists"] is True
    assert payload["base_sha256"] == _sha("original\n")


def test_new_file_proposal_marks_base_missing_explicitly(tmp_path):
    service, _ = _service(tmp_path)
    item = service.propose_file_update(path="fresh.txt", new_content="hi")
    payload = item["payload"]
    assert payload["base_exists"] is False
    # explicit sentinel — not the hash of the empty string
    assert payload["base_sha256"] == ""
    assert payload["base_sha256"] != _sha("")


# ── approve-time conflict detection (update) ─────────────────────────────

def test_approve_conflicts_when_file_modified_after_proposal(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, queue = _service(tmp_path)
    item = service.propose_file_update(path="site.html", new_content="<new>")
    # user edits the file out-of-band after staging
    (tmp_path / "site.html").write_text("<user edit>", encoding="utf-8")

    with pytest.raises(ProposalConflictError) as excinfo:
        service.approve_and_apply(item["id"])
    err = excinfo.value
    assert err.reason == "file_modified_since_proposal"
    assert err.current_sha256 == _sha("<user edit>")
    assert err.rebase_hint
    # the user's edit survives and the proposal stays open
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<user edit>"
    assert queue.items[item["id"]]["status"] == "pending"


def test_approve_conflicts_when_file_deleted_after_proposal(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, _ = _service(tmp_path)
    item = service.propose_file_update(path="site.html", new_content="<new>")
    (tmp_path / "site.html").unlink()

    with pytest.raises(ProposalConflictError) as excinfo:
        service.approve_and_apply(item["id"])
    assert excinfo.value.reason == "file_deleted_since_proposal"
    assert not (tmp_path / "site.html").exists()


def test_approve_conflicts_when_file_recreated_with_different_content(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, _ = _service(tmp_path)
    item = service.propose_file_update(path="site.html", new_content="<new>")
    (tmp_path / "site.html").unlink()
    (tmp_path / "site.html").write_text("<other>", encoding="utf-8")

    with pytest.raises(ProposalConflictError) as excinfo:
        service.approve_and_apply(item["id"])
    assert excinfo.value.reason == "file_modified_since_proposal"
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<other>"


def test_recreation_with_identical_content_still_applies(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, _ = _service(tmp_path)
    item = service.propose_file_update(path="site.html", new_content="<new>")
    (tmp_path / "site.html").unlink()
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")

    result = service.approve_and_apply(item["id"])
    assert result["applied"] is True
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<new>"


# ── new-file proposals ───────────────────────────────────────────────────

def test_new_file_proposal_applies_when_still_absent(tmp_path):
    service, _ = _service(tmp_path)
    item = service.propose_file_update(path="fresh.txt", new_content="hi")
    result = service.approve_and_apply(item["id"])
    assert result["applied"] is True
    assert (tmp_path / "fresh.txt").read_text(encoding="utf-8") == "hi"


def test_new_file_proposal_conflicts_when_file_appeared(tmp_path):
    service, queue = _service(tmp_path)
    item = service.propose_file_update(path="fresh.txt", new_content="hi")
    (tmp_path / "fresh.txt").write_text("user made this", encoding="utf-8")

    with pytest.raises(ProposalConflictError) as excinfo:
        service.approve_and_apply(item["id"])
    assert excinfo.value.reason == "file_created_since_proposal"
    assert (tmp_path / "fresh.txt").read_text(encoding="utf-8") == "user made this"
    assert queue.items[item["id"]]["status"] == "pending"


# ── delete proposals ─────────────────────────────────────────────────────

def test_delete_proposal_conflicts_when_modified_after_proposal(tmp_path):
    (tmp_path / "gone.txt").write_text("bye", encoding="utf-8")
    service, queue = _service(tmp_path)
    item = _stage_delete(queue, "gone.txt", "bye")
    (tmp_path / "gone.txt").write_text("actually keep me", encoding="utf-8")

    with pytest.raises(ProposalConflictError) as excinfo:
        service.approve_and_apply(item["id"])
    assert excinfo.value.reason == "file_modified_since_proposal"
    assert (tmp_path / "gone.txt").read_text(encoding="utf-8") == "actually keep me"
    assert queue.items[item["id"]]["status"] == "pending"


def test_delete_proposal_unchanged_still_applies(tmp_path):
    (tmp_path / "gone.txt").write_text("bye", encoding="utf-8")
    service, queue = _service(tmp_path)
    item = _stage_delete(queue, "gone.txt", "bye")
    result = service.approve_and_apply(item["id"])
    assert result["applied"] is True
    assert not (tmp_path / "gone.txt").exists()


# ── duplicate / concurrent approval ──────────────────────────────────────

def test_duplicate_approve_is_rejected_and_does_not_rewrite(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, _ = _service(tmp_path)
    item = service.propose_file_update(path="site.html", new_content="<new>")
    assert service.approve_and_apply(item["id"])["applied"] is True
    # user keeps editing after the apply
    (tmp_path / "site.html").write_text("<post-apply edit>", encoding="utf-8")

    with pytest.raises(ProposalConflictError) as excinfo:
        service.approve_and_apply(item["id"])
    assert excinfo.value.reason == "already_approved"
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<post-apply edit>"


def test_approve_of_dismissed_proposal_is_rejected(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, _ = _service(tmp_path)
    item = service.propose_file_update(path="site.html", new_content="<new>")
    service.reject(item["id"])

    with pytest.raises(ProposalConflictError) as excinfo:
        service.approve_and_apply(item["id"])
    assert excinfo.value.reason == "already_dismissed"
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<old>"


def test_concurrent_approvals_apply_exactly_once(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, _ = _service(tmp_path)
    item = service.propose_file_update(path="site.html", new_content="<new>")

    outcomes = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            outcomes.append(service.approve_and_apply(item["id"])["applied"])
        except ProposalConflictError:
            outcomes.append("conflict")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count(True) == 1
    assert outcomes.count("conflict") == 7
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<new>"


# ── backward compatibility ───────────────────────────────────────────────

def test_legacy_proposal_without_base_snapshot_still_applies(tmp_path):
    """Items staged before this fix lack the base fields — keep the
    historical apply-as-reviewed behavior instead of rejecting them."""
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, queue = _service(tmp_path)
    legacy = queue.create(
        title="파일 수정 제안: site.html",
        source="change_proposal",
        kind="file_update",
        payload={"path": "site.html", "new_content": "<new>", "diff": [], "tier": "small"},
        provenance={},
    )
    result = service.approve_and_apply(legacy["id"])
    assert result["applied"] is True
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<new>"


# ── API layer: conflict → HTTP 409 with rebase hint ──────────────────────

def _api_env(tmp_path):
    store = WorkspaceOSStore(tmp_path / "data")
    queue = ReviewQueueService(store=store)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)

    def resolve(path=""):
        candidate = (workspace / path).resolve()
        assert str(candidate).startswith(str(workspace))
        return candidate

    proposals = ChangeProposalService(review_queue=queue, resolve_path=resolve)
    app = FastAPI()
    app.include_router(create_review_queue_router(
        service=queue,
        require_user=lambda _req: "u@x.com",
        gate_read=lambda _req: "personal",
        gate_write=lambda _req: "personal",
        run_review_item=lambda *_a, **_k: None,
        append_audit_event=lambda *_a, **_k: None,
        change_proposals=proposals,
    ))
    return TestClient(app), queue, proposals, workspace


def test_review_center_approve_returns_409_on_drift(tmp_path):
    client, queue, proposals, workspace = _api_env(tmp_path)
    (workspace / "site.html").write_text("<old>", encoding="utf-8")
    item = proposals.propose_file_update(
        path="site.html", new_content="<new>",
        user_email="u@x.com", workspace_id="personal",
    )
    (workspace / "site.html").write_text("<user edit>", encoding="utf-8")

    res = client.post(f"/automation/reviews/{item['id']}/approve")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "change_proposal_conflict"
    assert detail["conflict"] is True
    assert detail["reason"] == "file_modified_since_proposal"
    assert detail["path"] == "site.html"
    assert detail["current_sha256"] == _sha("<user edit>")
    assert detail["rebase_hint"]
    # user edit preserved, proposal still open for reject/re-stage
    assert (workspace / "site.html").read_text(encoding="utf-8") == "<user edit>"
    stored = queue.get(item["id"], workspace_id="personal")
    assert stored["effective_status"] == "pending"


def test_review_center_approve_clean_path_still_applies(tmp_path):
    client, _, proposals, workspace = _api_env(tmp_path)
    (workspace / "site.html").write_text("<old>", encoding="utf-8")
    item = proposals.propose_file_update(
        path="site.html", new_content="<new>",
        user_email="u@x.com", workspace_id="personal",
    )
    res = client.post(f"/automation/reviews/{item['id']}/approve")
    assert res.status_code == 200
    assert res.json()["status"] == "approved"
    assert (workspace / "site.html").read_text(encoding="utf-8") == "<new>"
