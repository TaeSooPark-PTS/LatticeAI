"""Permission mode decision table (v9.9.8)."""

from __future__ import annotations

import pytest

from latticeai.core.permission_mode import (
    PermissionMode,
    effective_auto_approve,
    is_circuit_breaker,
    mode_contract,
    normalize_mode,
    plan_requires_approval,
    should_stage_proposal,
)


def _write_policy(**overrides):
    base = {
        "risk": "write",
        "destructive": False,
        "shell": False,
        "network": False,
        "auto_approve": False,
        "sandbox": "workspace",
        "rollback": "git",
    }
    base.update(overrides)
    return base


def test_normalize_aliases():
    assert normalize_mode("YOLO") is PermissionMode.BYPASS
    assert normalize_mode("acceptEdits") is PermissionMode.TRUSTED
    assert normalize_mode("nope") is PermissionMode.STRICT


def test_strict_keeps_writes_gated():
    assert effective_auto_approve("strict", "write_file", _write_policy()) is False
    assert effective_auto_approve("strict", "knowledge_search", _write_policy(risk="read")) is False


def test_trusted_autos_workspace_and_knowledge():
    assert effective_auto_approve("trusted", "write_file", _write_policy()) is True
    assert effective_auto_approve("trusted", "edit_file", _write_policy()) is True
    assert effective_auto_approve(
        "trusted", "knowledge_search", _write_policy(risk="read", auto_approve=False)
    ) is True
    assert effective_auto_approve(
        "trusted", "computer_screenshot", _write_policy(risk="read", sandbox="system")
    ) is True
    assert effective_auto_approve(
        "trusted", "computer_click", _write_policy(risk="exec", sandbox="system")
    ) is False
    assert effective_auto_approve(
        "trusted", "run_command", _write_policy(risk="exec", shell=True)
    ) is False


def test_bypass_autos_exec_inside_workspace():
    assert effective_auto_approve(
        "bypass", "run_command", _write_policy(risk="exec", shell=True)
    ) is True
    assert effective_auto_approve(
        "bypass", "computer_click", _write_policy(risk="exec", sandbox="system")
    ) is True


def test_circuit_breaker_root_delete():
    reason = is_circuit_breaker(
        "run_command",
        _write_policy(risk="exec", shell=True),
        {"command": "rm -rf /"},
    )
    assert reason is not None
    assert "circuit breaker" in reason


def test_circuit_breaker_destructive_policy():
    reason = is_circuit_breaker(
        "delete_file",
        _write_policy(risk="destructive", destructive=True),
        {"path": "notes.md"},
    )
    assert reason is not None


def test_proposal_staging_only_in_strict():
    assert should_stage_proposal("strict", proposal_required=True) is True
    assert should_stage_proposal("trusted", proposal_required=True) is False
    assert should_stage_proposal("bypass", proposal_required=True) is False
    assert should_stage_proposal("strict", proposal_required=False) is False


def test_plan_gate_bypass_never_blocks():
    assert plan_requires_approval("bypass", non_auto_steps=["run_command"]) is False
    assert plan_requires_approval("strict", non_auto_steps=["run_command"]) is True
    assert plan_requires_approval("trusted", non_auto_steps=[]) is False


def test_mode_contract_shape():
    c = mode_contract("trusted")
    assert c["mode"] == "trusted"
    assert c["workspace_writes_auto"] is True
    assert c["exec_auto"] is False
    assert c["circuit_breakers"] is True
