"""wp35: hybrid local+cloud boundary services.

The bridges take their adapter at construction and the policy/boundary stores
take ``data_dir`` — both are faked at that seam so nothing here touches a
network, a provider SDK, or a shared data dir.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from latticeai.core.network_boundary import NetworkBoundaryMode
from latticeai.services import cloud_extraction as extraction_mod
from latticeai.services.cloud_streaming import (
    CloudLLMAdapter,
    CloudResponseIngestor,
    CloudStreamingBridge,
    CloudTurnResult,
    KGExpansionPlan,
    plan_kg_expansion,
)
from latticeai.services.hybrid_context import (
    MinimalContext,
    SupportsHybridSearch,
    build_minimal_context,
)
from latticeai.services.hybrid_policy import HybridPolicyService
from latticeai.services.multimodal_streaming import (
    MultimodalAdapter,
    MultimodalStreamingBridge,
    MultimodalTurnResult,
)
from latticeai.services.network_boundary_service import NetworkBoundaryService


class _Store:
    """Minimal ``SupportsHybridSearch`` stand-in."""

    def __init__(self, matches: List[Dict[str, Any]] | None = None, boom: bool = False):
        self.matches = matches or []
        self.boom = boom

    def hybrid_search(self, query, **kwargs):
        if self.boom:
            raise RuntimeError("index unavailable")
        return {"mode": "hybrid", "matches": list(self.matches)}


# ── hybrid_context ────────────────────────────────────────────────────────────


def test_minimal_context_to_dict_is_a_plain_snapshot():
    context = MinimalContext(query="q", keywords=["a"], node_ids=["n1"])

    payload = context.to_dict()

    assert payload["query"] == "q"
    assert payload["keywords"] == ["a"]
    assert payload["node_ids"] == ["n1"]
    assert payload["token_estimate"] == 0


def test_supports_hybrid_search_protocol_methods_are_declarations_only():
    assert SupportsHybridSearch.hybrid_search(None, "q") is None
    assert SupportsHybridSearch.context_for_query_with_meta(None, "q") is None


def test_empty_query_and_missing_store_return_the_honest_empty_context():
    empty = build_minimal_context("", store=_Store())

    assert empty.node_ids == []
    assert empty.keywords == []
    assert empty.quality["reason"] == "no store or empty query"


def test_keyword_extraction_dedupes_and_stops_at_the_limit():
    message = "alpha alpha " + " ".join(f"tok{i}" for i in range(20))

    context = build_minimal_context(message, store=_Store())

    assert context.keywords[0] == "alpha"
    assert context.keywords.count("alpha") == 1
    assert len(context.keywords) == 12


def test_no_matches_yields_zero_token_estimate():
    context = build_minimal_context("ship the release", store=_Store())

    assert context.node_ids == []
    assert context.compact_text == ""
    assert context.token_estimate == 0
    assert context.quality["nodes"] == 0


def test_matches_without_an_id_are_skipped():
    store = _Store(
        [
            {"type": "Concept", "title": "no id here", "summary": "s"},
            {"node_id": "n1", "type": "Decision", "title": "keep", "summary": "s"},
        ]
    )

    context = build_minimal_context("release", store=store)

    assert context.node_ids == ["n1"]
    assert "keep" in context.compact_text


def test_failed_retrieval_sends_no_context_at_all():
    context = build_minimal_context("release", store=_Store(boom=True))

    assert context.node_ids == []
    assert context.compact_text == ""
    assert context.quality["nodes"] == 0


# ── hybrid_policy ─────────────────────────────────────────────────────────────


def test_hybrid_policy_rebinds_data_dir_and_audit(tmp_path: Path):
    service = HybridPolicyService(data_dir=tmp_path / "old")
    events: list = []

    service.rebind_data_dir(tmp_path / "new")
    service.rebind_audit(lambda event, **kw: events.append(event))
    service.set_policy({"auto_commit": True})

    assert (tmp_path / "new" / "hybrid_policy.json").is_file()
    assert not (tmp_path / "old").exists()
    assert events == ["hybrid_policy_changed"]


def test_hybrid_policy_read_survives_corrupt_and_non_dict_files(tmp_path: Path):
    service = HybridPolicyService(data_dir=tmp_path)
    path = tmp_path / "hybrid_policy.json"

    path.write_text("{ not json", encoding="utf-8")
    assert service.resolve()["auto_commit"] is False

    path.write_text("[]", encoding="utf-8")
    assert service.resolve()["allow_multimodal"] is False


def test_workspace_override_wins_and_bad_confidence_falls_back(tmp_path: Path):
    path = tmp_path / "hybrid_policy.json"
    path.write_text(
        json.dumps(
            {
                "default": {"auto_commit": False},
                "users": {"a@b.c": {"auto_commit": False}},
                "workspaces": {
                    "team": {
                        "auto_commit": True,
                        "min_extraction_confidence": "not-a-float",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    service = HybridPolicyService(data_dir=tmp_path)

    resolved = service.resolve(user_email="a@b.c", workspace_id="team")

    assert resolved["auto_commit"] is True
    assert resolved["min_extraction_confidence"] == 0.55
    # Hard circuit breakers are always unioned in, whatever the file says.
    assert "sensitive" in resolved["blocked_metadata_flags"]


def test_set_policy_targets_workspace_then_default_buckets(tmp_path: Path):
    service = HybridPolicyService(data_dir=tmp_path)

    service.set_policy({"allow_multimodal": True, "ignored": 1}, workspace_id="team")
    service.set_policy({"auto_commit": True})

    stored = json.loads((tmp_path / "hybrid_policy.json").read_text(encoding="utf-8"))
    assert stored["workspaces"]["team"] == {"allow_multimodal": True}
    assert stored["default"]["auto_commit"] is True
    assert service.resolve(workspace_id="team")["allow_multimodal"] is True
    assert service.resolve()["allow_multimodal"] is False


# ── network_boundary_service ──────────────────────────────────────────────────


def test_network_boundary_rebinds_data_dir_and_audit(tmp_path: Path):
    service = NetworkBoundaryService(data_dir=tmp_path / "old")
    events: list = []

    service.rebind_data_dir(tmp_path / "new")
    service.rebind_audit(lambda event, **kw: events.append(kw))
    contract = service.set_mode("cloud_allowed", acknowledge_risk=True)

    assert contract["mode"] == "cloud_allowed"
    stored = json.loads(
        (tmp_path / "new" / "network_boundary.json").read_text(encoding="utf-8")
    )
    assert stored["default"] == "cloud_allowed"
    assert events[0]["previous"] == "local_only"


def test_network_boundary_read_survives_corrupt_and_non_dict_files(tmp_path: Path):
    service = NetworkBoundaryService(data_dir=tmp_path)
    path = tmp_path / "network_boundary.json"

    path.write_text("{ not json", encoding="utf-8")
    assert service.resolve() is NetworkBoundaryMode.LOCAL_ONLY

    path.write_text("[]", encoding="utf-8")
    assert service.resolve() is NetworkBoundaryMode.LOCAL_ONLY


# ── cloud_streaming ───────────────────────────────────────────────────────────


def test_cloud_turn_result_to_dict_and_protocol_declaration():
    result = CloudTurnResult(
        user_message="u", answer_text="a", sent_node_ids=["n1"], provider="p"
    )

    assert result.to_dict()["sent_node_ids"] == ["n1"]
    assert CloudLLMAdapter.stream(None, system="s", user="u", context="c") is None


def test_cloud_bridge_refuses_to_stream_while_local_only():
    bridge = CloudStreamingBridge()

    with pytest.raises(PermissionError, match="local_only"):
        asyncio.run(
            bridge.run_turn(
                user_message="hi",
                minimal=MinimalContext(query="hi"),
                mode="local_only",
            )
        )


def test_cloud_bridge_without_adapter_reports_what_would_have_been_sent():
    bridge = CloudStreamingBridge()

    result = asyncio.run(
        bridge.run_turn(
            user_message="hi",
            minimal=MinimalContext(query="hi", node_ids=["n1", "n2"]),
            mode=NetworkBoundaryMode.CLOUD_ALLOWED,
            model="fake-model",
        )
    )

    assert result.provider == "none"
    assert result.model == "fake-model"
    assert result.sent_node_ids == ["n1", "n2"]
    assert "2 local node(s)" in result.answer_text


def test_ingestor_records_a_review_error_without_losing_the_plan():
    class BrokenQueue:
        def create(self, **kwargs):
            raise RuntimeError("queue offline")

    ingestor = CloudResponseIngestor(review_queue=BrokenQueue())

    result = ingestor.ingest(plan_kg_expansion(CloudTurnResult("u", "a")))

    assert result["status"] == "staged"
    assert result["review_error"] == "queue offline"
    assert result["review_item_id"] is None


def test_ingestor_auto_commit_writes_through_a_known_store_api():
    written: list = []

    class Store:
        def upsert_nodes(self, nodes, edges):
            written.append((len(nodes), len(edges)))

    plan = plan_kg_expansion(CloudTurnResult("u", "a", sent_node_ids=["n1"]))
    plan.auto_commit = True

    result = CloudResponseIngestor(Store()).ingest(plan)

    assert result["status"] == "accepted"
    assert result["written_nodes"] == 1
    assert result["written_edges"] == 1
    assert written == [(1, 1)]


def test_ingestor_soft_accepts_a_store_without_a_write_api():
    plan = KGExpansionPlan(conversation_title="t", new_nodes=[{"id": "a"}])
    plan.auto_commit = True

    result = CloudResponseIngestor(object()).ingest(plan)

    # No review queue was bound, so "staged" is the honest status here; the
    # node/edge counts still report what a soft accept would have written.
    assert result["status"] == "staged"
    assert result["written_nodes"] == 1
    assert result["written_edges"] == 0


def test_ingestor_records_a_write_error_when_the_store_raises():
    class Store:
        def upsert_nodes(self, nodes, edges):
            raise RuntimeError("disk full")

    plan = KGExpansionPlan(conversation_title="t", new_nodes=[{"id": "a"}])
    plan.auto_commit = True

    result = CloudResponseIngestor(Store()).ingest(plan)

    assert result["write_error"] == "disk full"
    assert result["written_nodes"] == 0


def test_ingestor_without_any_sink_explains_itself():
    result = CloudResponseIngestor().ingest(KGExpansionPlan(conversation_title="t"))

    assert result["status"] == "staged"
    assert result["reason"] == "no store or review_queue bound"


# ── multimodal_streaming ──────────────────────────────────────────────────────


def test_multimodal_result_to_dict_and_protocol_declaration():
    result = MultimodalTurnResult(user_message="u", media_urls=["file:///a.mp4"])

    assert result.to_dict()["media_urls"] == ["file:///a.mp4"]
    assert MultimodalAdapter.stream_media(None, prompt="p", context="c") is None


def test_multimodal_refuses_while_local_only():
    with pytest.raises(PermissionError, match="local_only"):
        asyncio.run(
            MultimodalStreamingBridge().run_turn(
                user_message="a video please",
                minimal=MinimalContext(query="a video please"),
                mode="local_only",
                allow_multimodal=True,
            )
        )


def test_multimodal_streams_media_and_ignores_malformed_events():
    class Adapter:
        provider_name = "fake-media"
        default_model = "fake-v1"

        async def stream_media(self, *, prompt, context, model=None):
            yield "not-a-dict"
            yield {"text": "rendering"}
            yield {"media_url": "file:///out.mp4", "text": "done"}

    result = asyncio.run(
        MultimodalStreamingBridge(Adapter()).run_turn(
            user_message="a video please",
            minimal=MinimalContext(query="a video please", node_ids=["n1"]),
            mode=NetworkBoundaryMode.CLOUD_ALLOWED,
            allow_multimodal=True,
        )
    )

    assert result.media_urls == ["file:///out.mp4"]
    assert result.answer_text == "rendering\ndone"
    assert result.provider == "fake-media"
    assert result.model == "fake-v1"
    assert result.sent_node_ids == ["n1"]


# ── cloud_extraction ──────────────────────────────────────────────────────────


def test_extraction_skips_empty_and_duplicate_candidates():
    answer = "**  **\n**Alpha**\n**Alpha**\n"

    candidates = extraction_mod.extract_candidates(answer)

    assert [c["title"] for c in candidates] == ["Alpha"]
    assert candidates[0]["type"] == "Concept"


@pytest.mark.parametrize(
    ("answer", "expected_type"),
    [
        ("Decision: one\nDecision: two\n", "Decision"),
        ("todo: one\ntodo: two\n", "Task"),
        ("concept: one\nconcept: two\n", "Concept"),
    ],
)
def test_extraction_stops_at_the_limit_in_every_section(answer, expected_type):
    candidates = extraction_mod.extract_candidates(answer, limit=1)

    assert len(candidates) == 1
    assert candidates[0]["type"] == expected_type
