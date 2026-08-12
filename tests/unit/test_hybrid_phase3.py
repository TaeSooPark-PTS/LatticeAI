"""Phase 3 hybrid tests: policy and review-queue ingest."""

from __future__ import annotations

from pathlib import Path

from latticeai.services.cloud_streaming import (
    CloudResponseIngestor,
    CloudTurnResult,
    plan_kg_expansion,
)
from latticeai.services.hybrid_policy import HybridPolicyService


def test_policy_defaults_and_override(tmp_path: Path):
    svc = HybridPolicyService(data_dir=tmp_path)
    base = svc.resolve(user_email="a@b.c")
    assert base["auto_commit"] is False
    assert base["allow_multimodal"] is False
    assert "sensitive" in base["blocked_metadata_flags"]

    updated = svc.set_policy(
        {"auto_commit": True, "allow_multimodal": True, "blocked_node_types": ["Secret"]},
        user_email="a@b.c",
    )
    assert updated["auto_commit"] is True
    assert updated["allow_multimodal"] is True
    assert "Secret" in updated["blocked_node_types"]


class _FakeReview:
    def __init__(self):
        self.items = []

    def create(self, **kwargs):
        item = {"id": f"rv-{len(self.items)+1}", **kwargs}
        self.items.append(item)
        return item


def test_ingestor_queues_review_item():
    review = _FakeReview()
    plan = plan_kg_expansion(
        CloudTurnResult(
            user_message="hi",
            answer_text="hello",
            sent_node_ids=["a"],
            provider="t",
            model="m",
        )
    )
    status = CloudResponseIngestor(review_queue=review, user_email="u@x").ingest(plan)
    assert status["status"] == "queued_for_review"
    assert status["review_item_id"]
    assert review.items[0]["source"] == "change_proposal"
