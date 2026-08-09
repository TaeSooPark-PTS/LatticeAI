"""wp24 coverage — ``lattice_brain.runtime`` contracts + lazy package exports.

``agent-run-contract/v1`` is the envelope four observability surfaces share, so
what matters here is what it *refuses*: a kind the family does not own, a
record whose ``schema_version`` contradicts its ``kind``, and a record carrying
no envelope at all. The package ``__getattr__`` is the other half — every name
on ``__all__`` resolves lazily to the module that owns it, and nothing else
resolves at all.
"""

from __future__ import annotations

import pytest

from lattice_brain import runtime as runtime_pkg
from lattice_brain.runtime import contracts


def test_stamp_contract_refuses_a_kind_outside_the_family():
    with pytest.raises(ValueError, match="unknown contract kind"):
        contracts.stamp_contract({}, kind="telemetry", identity="x", status="ok")


def test_is_contract_member_checks_kind_and_schema_agreement():
    valid = contracts.stamp_contract(
        {"goal": "audit"}, kind="audit_event", identity="e-1", status="tool_call",
    )
    assert contracts.is_contract_member(valid) is True

    unknown_kind = dict(valid, kind="telemetry")
    assert contracts.is_contract_member(unknown_kind) is False

    mismatched_schema = dict(valid, schema_version=contracts.AGENT_RUN_SCHEMA)
    assert contracts.is_contract_member(mismatched_schema) is False

    assert contracts.is_contract_member("not-a-record") is False
    assert contracts.is_contract_member({"family": "other/v9"}) is False


def test_require_contract_names_the_missing_envelope():
    stamped = contracts.stamp_contract(
        {"run_id": "r-1"}, kind="agent_run", identity="r-1", status="ok",
    )
    assert contracts.require_contract({"contract": stamped})["id"] == "r-1"

    with pytest.raises(ValueError, match="agent-run-contract/v1"):
        contracts.require_contract({"run_id": "r-1", "status": "ok"})

    assert contracts.extract_contract({"run_id": "r-1"}) is None


def test_runtime_package_resolves_every_public_name_lazily():
    from lattice_brain.runtime.agent_runtime import (
        AgentRuntime,
        AgentRuntimeUnavailable,
    )
    from lattice_brain.runtime.contracts import RuntimeBoundaryProtocol
    from lattice_brain.runtime.hooks import HooksRegistry, dispatch_tool
    from lattice_brain.runtime.multi_agent import MultiAgentOrchestrator

    assert runtime_pkg.AgentRuntime is AgentRuntime
    assert runtime_pkg.AgentRuntimeUnavailable is AgentRuntimeUnavailable
    assert runtime_pkg.MultiAgentOrchestrator is MultiAgentOrchestrator
    assert runtime_pkg.RuntimeBoundaryProtocol is RuntimeBoundaryProtocol
    assert runtime_pkg.HooksRegistry is HooksRegistry
    assert runtime_pkg.dispatch_tool is dispatch_tool


def test_runtime_package_refuses_a_name_it_does_not_export():
    unexported = "SingleAgentRuntime"
    assert unexported not in runtime_pkg.__all__
    with pytest.raises(AttributeError, match=unexported):
        getattr(runtime_pkg, unexported)
