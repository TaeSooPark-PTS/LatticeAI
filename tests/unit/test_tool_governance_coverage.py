from __future__ import annotations

import pytest

from latticeai.core.tool_governor import (
    EXISTING_CONTENT_UPDATE,
    EXTERNAL_SIDE_EFFECT,
    MUTATING_TOOL_INVENTORY,
    PROPOSAL_CAPABLE_TOOLS,
    assert_governance_coverage,
    classify_tool_call,
)
from latticeai.core.tool_registry import TOOL_GOVERNANCE

_READ_RISKS = {"read"}


def _side_effecting_tools() -> list[str]:
    names = []
    for name, policy in TOOL_GOVERNANCE.items():
        risk = str(policy.get("risk") or "")
        if risk in _READ_RISKS and not policy.get("destructive"):
            continue
        names.append(name)
    return names


def test_every_side_effecting_tool_is_classified():
    assert_governance_coverage(_side_effecting_tools())


def test_unclassified_mutator_fails_closed():
    with pytest.raises(ValueError):
        assert_governance_coverage(["write_file", "totally_new_mutator_9000"])


def test_existing_content_updates_are_capable_or_fail_closed():
    for name, category in MUTATING_TOOL_INVENTORY.items():
        if category != EXISTING_CONTENT_UPDATE:
            continue
        verdict = classify_tool_call(
            name,
            {"path": "already_here.txt"},
            policy=dict(TOOL_GOVERNANCE.get(name, {})),
            path_exists=lambda _p: True,
        )
        assert verdict["proposal_required"] is True, name
        assert verdict["proposal_supported"] or verdict["fail_closed"], name


def test_proposal_capable_tools_are_not_fail_closed_on_overwrite():
    for name in PROPOSAL_CAPABLE_TOOLS:
        verdict = classify_tool_call(
            name,
            {"path": "existing.txt", "old_string": "a", "new_string": "b"},
            policy=dict(TOOL_GOVERNANCE.get(name, {})),
            path_exists=lambda _p: True,
        )
        assert verdict["proposal_required"] is True, name
        assert verdict["fail_closed"] is False, name


def test_binary_creators_fail_closed_on_overwrite():
    for name in ("create_docx", "create_xlsx", "create_pptx", "create_pdf"):
        verdict = classify_tool_call(
            name,
            {"path": "report.docx"},
            policy=dict(TOOL_GOVERNANCE.get(name, {})),
            path_exists=lambda _p: True,
        )
        assert verdict["fail_closed"] is True, name


def test_new_file_creation_is_additive_not_blocked():
    for name in ("create_docx", "local_write", "write_file"):
        verdict = classify_tool_call(
            name,
            {"path": "brand_new_file.txt"},
            policy=dict(TOOL_GOVERNANCE.get(name, {})),
            path_exists=lambda _p: False,
        )
        assert verdict["proposal_required"] is False, name
        assert verdict["fail_closed"] is False, name


def test_external_side_effects_are_not_proposal_routed():
    for name, category in MUTATING_TOOL_INVENTORY.items():
        if category != EXTERNAL_SIDE_EFFECT:
            continue
        verdict = classify_tool_call(
            name,
            {},
            policy=dict(TOOL_GOVERNANCE.get(name, {})),
        )
        assert verdict["proposal_required"] is False, name
