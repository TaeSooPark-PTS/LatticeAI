"""Review Center ↔ change governor loop closure tests.

v9.6.0 shipped proposal staging, but the Review Center approve path only
flipped the item status — nothing hit disk. These tests pin the closed loop:

* agent-governor proposals appear in the shared review queue with full
  context (diff, tier, tool, risk, change class);
* approving from the Review Center (``/automation/reviews``) *applies* the
  staged content via the same ChangeProposalService path as /api/proposals;
* rejecting keeps a reason in provenance and never touches disk;
* counts endpoints feed the pending badge.
"""

from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.change_proposals import create_change_proposals_router
from latticeai.api.review_queue import create_review_queue_router
from latticeai.core.workspace_os import WorkspaceOSStore
from latticeai.services.change_proposals import ChangeProposalService
from latticeai.services.review_queue import ReviewQueueService


def _env(tmp_path, clock=None):
    store = WorkspaceOSStore(tmp_path / "data")
    queue = ReviewQueueService(store=store, clock=clock or datetime.now)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)

    def resolve(path=""):
        candidate = (workspace / path).resolve()
        assert str(candidate).startswith(str(workspace))
        return candidate

    proposals = ChangeProposalService(review_queue=queue, resolve_path=resolve)
    return store, queue, proposals, workspace


def _client(queue, proposals):
    app = FastAPI()
    app.include_router(create_review_queue_router(
        service=queue,
        require_user=lambda _req: "u@x.com",
        gate_read=lambda _req: "personal",
        gate_write=lambda _req: "personal",
        run_review_item=lambda *_a, **_k: {"run": {"id": "run-api"}},
        append_audit_event=lambda *_a, **_k: None,
        change_proposals=proposals,
    ))
    app.include_router(create_change_proposals_router(
        service=proposals,
        require_user=lambda _req: "u@x.com",
        gate_read=lambda _req: "personal",
        gate_write=lambda _req: "personal",
    ))
    return TestClient(app)


def _stage_overwrite(proposals, workspace, name="site.html", before="<old>", after="<new>"):
    (workspace / name).write_text(before, encoding="utf-8")
    verdict = proposals.review(
        "write_file", {"path": name, "content": after},
        policy={"risk": "write"}, user_email="u@x.com", workspace_id="personal",
    )
    assert verdict["decision"] == "proposed"
    return verdict["proposal"]


# ── proposal context surfaced to the Review Center ───────────────────────

def test_governor_proposal_carries_full_review_context(tmp_path):
    _, queue, proposals, workspace = _env(tmp_path)
    item = _stage_overwrite(proposals, workspace)

    assert item["source"] == "change_proposal"
    payload = item["payload"]
    assert payload["tier"] == "small"
    assert any(line.startswith("-<old>") for line in payload["diff"])
    provenance = item["provenance"]
    assert provenance["proposed_by"] == "agent"
    assert provenance["tool"] == "write_file"
    assert provenance["risk"] == "write"
    assert provenance["change_class"] == "mutation"
    assert provenance["source_detail"] == "agent change governor"


def test_proposal_visible_in_review_queue_list_and_counts(tmp_path):
    _, queue, proposals, workspace = _env(tmp_path)
    _stage_overwrite(proposals, workspace)
    client = _client(queue, proposals)

    listed = client.get("/automation/reviews", params={"source": "change_proposal"})
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["payload"]["diff"]

    counts = client.get("/automation/reviews/counts")
    assert counts.status_code == 200
    body = counts.json()
    assert body["pending"] == 1
    assert body["pending_by_source"] == {"change_proposal": 1}

    proposal_counts = client.get("/api/proposals/counts")
    assert proposal_counts.status_code == 200
    assert proposal_counts.json() == {"pending": 1}


def test_counts_treats_expired_snooze_as_pending(tmp_path):
    now = datetime.now()
    _, queue, proposals, workspace = _env(tmp_path, clock=lambda: now)
    item = _stage_overwrite(proposals, workspace)
    queue.snooze(item["id"], until=(now - timedelta(minutes=1)).isoformat())

    counts = queue.counts(workspace_id="personal")
    assert counts["pending"] == 1
    assert counts["snoozed"] == 0


# ── approve from the Review Center applies the staged change ─────────────

def test_review_center_approve_applies_staged_content(tmp_path):
    _, queue, proposals, workspace = _env(tmp_path)
    item = _stage_overwrite(proposals, workspace)
    client = _client(queue, proposals)

    res = client.post(f"/automation/reviews/{item['id']}/approve")
    assert res.status_code == 200
    assert res.json()["status"] == "approved"
    # loop closed: the staged content is on disk, exactly as reviewed
    assert (workspace / "site.html").read_text(encoding="utf-8") == "<new>"


def test_review_center_approve_of_approved_proposal_is_409(tmp_path):
    _, queue, proposals, workspace = _env(tmp_path)
    item = _stage_overwrite(proposals, workspace)
    client = _client(queue, proposals)
    assert client.post(f"/automation/reviews/{item['id']}/approve").status_code == 200
    assert client.post(f"/automation/reviews/{item['id']}/approve").status_code == 409


def test_review_center_approve_without_wiring_still_transitions(tmp_path):
    # Back-compat: router without change_proposals behaves exactly as before.
    _, queue, proposals, workspace = _env(tmp_path)
    item = _stage_overwrite(proposals, workspace)
    app = FastAPI()
    app.include_router(create_review_queue_router(
        service=queue,
        require_user=lambda _req: "u@x.com",
        gate_read=lambda _req: "personal",
        gate_write=lambda _req: "personal",
        run_review_item=lambda *_a, **_k: None,
        append_audit_event=lambda *_a, **_k: None,
    ))
    client = TestClient(app)
    res = client.post(f"/automation/reviews/{item['id']}/approve")
    assert res.status_code == 200
    # status-only transition (legacy) — disk untouched
    assert (workspace / "site.html").read_text(encoding="utf-8") == "<old>"


def test_non_proposal_approve_unaffected_by_wiring(tmp_path):
    _, queue, proposals, _ = _env(tmp_path)
    other = queue.create(
        title="digest", source="workflow_run", kind="suggestion",
        user_email="u@x.com", workspace_id="personal",
    )
    client = _client(queue, proposals)
    res = client.post(f"/automation/reviews/{other['id']}/approve")
    assert res.status_code == 200
    assert res.json()["status"] == "approved"


# ── reject with reason ───────────────────────────────────────────────────

def test_reject_with_reason_keeps_reason_and_disk_untouched(tmp_path):
    _, queue, proposals, workspace = _env(tmp_path)
    item = _stage_overwrite(proposals, workspace)
    client = _client(queue, proposals)

    res = client.post(
        f"/api/proposals/{item['id']}/reject", json={"reason": "wrong file"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["applied"] is False
    assert body["item"]["status"] == "dismissed"
    assert body["item"]["provenance"]["dismiss_reason"] == "wrong file"
    assert (workspace / "site.html").read_text(encoding="utf-8") == "<old>"


def test_reject_without_body_still_works(tmp_path):
    _, queue, proposals, workspace = _env(tmp_path)
    item = _stage_overwrite(proposals, workspace)
    client = _client(queue, proposals)
    res = client.post(f"/api/proposals/{item['id']}/reject")
    assert res.status_code == 200
    assert res.json()["item"]["status"] == "dismissed"


def test_review_dismiss_accepts_optional_reason_body(tmp_path):
    _, queue, proposals, workspace = _env(tmp_path)
    item = _stage_overwrite(proposals, workspace)
    client = _client(queue, proposals)
    res = client.post(
        f"/automation/reviews/{item['id']}/dismiss", json={"reason": "not needed"}
    )
    assert res.status_code == 200
    assert res.json()["provenance"]["dismiss_reason"] == "not needed"


# ── detail + source guards ───────────────────────────────────────────────

def test_proposal_detail_returns_diff_and_staged_content(tmp_path):
    _, queue, proposals, workspace = _env(tmp_path)
    item = _stage_overwrite(proposals, workspace)
    client = _client(queue, proposals)
    res = client.get(f"/api/proposals/{item['id']}")
    assert res.status_code == 200
    body = res.json()
    assert body["payload"]["new_content"] == "<new>"
    assert body["payload"]["diff"]


def test_proposal_endpoints_guard_non_proposal_items(tmp_path):
    _, queue, proposals, _ = _env(tmp_path)
    other = queue.create(
        title="digest", source="workflow_run", kind="suggestion",
        user_email="u@x.com", workspace_id="personal",
    )
    client = _client(queue, proposals)
    assert client.get(f"/api/proposals/{other['id']}").status_code == 404
    assert client.post(f"/api/proposals/{other['id']}/approve").status_code == 404
    assert client.post(f"/api/proposals/{other['id']}/reject").status_code == 404
    # and the non-proposal item was not dismissed by the failed reject
    assert queue.get(other["id"], workspace_id="personal")["status"] == "pending"
