"""Unit tests for hybrid NetworkBoundaryMode (Phase 0 + Phase 1 scaffolding)."""

from __future__ import annotations

from pathlib import Path

import pytest

from latticeai.core.network_boundary import (
    DEFAULT_NETWORK_MODE,
    NetworkBoundaryMode,
    is_node_blocked_for_cloud,
    network_mode_contract,
    normalize_network_mode,
)
from latticeai.services.hybrid_context import MinimalContext, build_minimal_context
from latticeai.services.cloud_streaming import plan_kg_expansion, CloudTurnResult
from latticeai.services.network_boundary_service import NetworkBoundaryService


def test_normalize_defaults_to_local_only():
    assert normalize_network_mode(None) == NetworkBoundaryMode.LOCAL_ONLY
    assert normalize_network_mode("") == NetworkBoundaryMode.LOCAL_ONLY
    assert normalize_network_mode("garbage") == NetworkBoundaryMode.LOCAL_ONLY
    assert DEFAULT_NETWORK_MODE == NetworkBoundaryMode.LOCAL_ONLY


def test_normalize_aliases():
    assert normalize_network_mode("cloud") == NetworkBoundaryMode.CLOUD_ALLOWED
    assert normalize_network_mode("hybrid") == NetworkBoundaryMode.CLOUD_ALLOWED
    assert normalize_network_mode("local") == NetworkBoundaryMode.LOCAL_ONLY


def test_contract_shape():
    contract = network_mode_contract(NetworkBoundaryMode.CLOUD_ALLOWED)
    assert contract["mode"] == "cloud_allowed"
    assert contract["allows_cloud"] is True
    assert contract["requires_ack"] is True


def test_sensitive_node_blocked():
    reason = is_node_blocked_for_cloud(
        {"type": "Note", "metadata": {"sensitive": True}}
    )
    assert reason is not None
    assert "sensitive" in reason


def test_service_requires_ack_for_cloud(tmp_path: Path):
    svc = NetworkBoundaryService(data_dir=tmp_path)
    with pytest.raises(PermissionError):
        svc.set_mode("cloud_allowed", user_email="a@b.c", acknowledge_risk=False)
    out = svc.set_mode(
        "cloud_allowed", user_email="a@b.c", acknowledge_risk=True
    )
    assert out["mode"] == "cloud_allowed"
    assert svc.resolve(user_email="a@b.c") == NetworkBoundaryMode.CLOUD_ALLOWED


def test_service_workspace_overrides_user(tmp_path: Path):
    svc = NetworkBoundaryService(data_dir=tmp_path)
    svc.set_mode("cloud_allowed", user_email="a@b.c", acknowledge_risk=True)
    svc.set_mode(
        "local_only", user_email="a@b.c", workspace_id="ws-1", acknowledge_risk=False
    )
    assert (
        svc.resolve(user_email="a@b.c", workspace_id="ws-1")
        == NetworkBoundaryMode.LOCAL_ONLY
    )
    assert svc.resolve(user_email="a@b.c") == NetworkBoundaryMode.CLOUD_ALLOWED


def test_build_minimal_context_empty_without_store():
    ctx = build_minimal_context("hello world", store=None)
    assert isinstance(ctx, MinimalContext)
    assert ctx.node_ids == []
    assert "hello" in ctx.keywords or "world" in ctx.keywords


def test_plan_kg_expansion_provenance():
    result = CloudTurnResult(
        user_message="what did we decide?",
        answer_text="We decided to ship hybrid mode.",
        sent_node_ids=["node:1", "node:2"],
        provider="test",
        model="test-model",
    )
    plan = plan_kg_expansion(result)
    assert plan.auto_commit is False
    assert len(plan.new_nodes) == 1
    assert plan.new_nodes[0]["metadata"]["derived_from_cloud"] is True
    assert len(plan.new_edges) == 2
    assert all(e["type"] == "grounded_on" for e in plan.new_edges)
    assert plan.provenance["sent_node_ids"] == ["node:1", "node:2"]
