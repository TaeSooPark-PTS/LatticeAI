"""Safety-kernel gates the Python worker still owns, asserted directly.

Until 11.8.0 these branches were reached only as a by-product of the
Python↔Rust parity grid in ``test_agent_kernel_parity_contract.py``: that
module imported ``scripts/generate_agent_parity_fixtures.py``, which drove
every tool × every argument shape × every mode and compared the answers to the
committed ``rust/fixtures/agent/golden/`` files. The generator's Python
subject — ``latticeai/core/agent_permission.py`` and the ``run_command``
allowlists — moved to ``lattice-agent``, the goldens froze at fc65e60, and the
whole module went with them.

What did **not** move is what this file covers: ``classify_tool_call``, the
``TOOL_GOVERNANCE`` table and its args-dependent override, and
``permission_mode``'s auto-approve rules. ``POST /agent/tool`` still consults
all three on every call. Sixty-odd fixture rows quietly exercising a branch is
not the same as a test that says which rule it is checking, so each of these
names one.
"""

from __future__ import annotations

import pytest

from latticeai.core.permission_mode import (
    PermissionMode,
    effective_auto_approve,
    normalize_mode,
)
from latticeai.core.tool_governor import (
    CHANGE_ADDITIVE,
    classify_tool_call,
)
from latticeai.core.tool_registry import (
    LOCAL_WRITE_BLOCKED_PREFIXES,
    TOOL_GOVERNANCE,
    ToolRegistry,
)


def _policy(**overrides) -> dict:
    base = {
        "risk": "write", "sandbox": "workspace", "auto_approve": False,
        "destructive": False, "shell": False, "network": False, "rollback": "none",
    }
    base.update(overrides)
    return base


# ── mode parsing ─────────────────────────────────────────────────────────────
def test_an_already_parsed_mode_is_returned_unchanged():
    """The runtime passes the enum back in; re-parsing its ``str()`` would
    turn ``PermissionMode.BYPASS`` into the strict fallback."""
    for mode in PermissionMode:
        assert normalize_mode(mode) is mode


def test_unknown_input_still_falls_back_to_strict():
    for junk in ("", "   ", "junk", "read-only", None, False, 0):
        assert normalize_mode(junk) is PermissionMode.STRICT


# ── auto-approve ─────────────────────────────────────────────────────────────
def test_a_tool_the_table_already_auto_approves_needs_no_mode_reasoning():
    """The first line of ``effective_auto_approve``: a policy that says yes is
    yes in every mode, including the strictest."""
    read_only = _policy(risk="read", auto_approve=True)
    for mode in ("strict", "trusted", "bypass"):
        assert effective_auto_approve(mode, "read_file", read_only) is True


def test_bypass_still_gates_a_system_sandbox_write():
    """``bypass`` skips *prompts*, not the sandbox boundary. A write or exec
    outside the workspace stays gated even there — the one thing that keeps
    "skip permissions" from meaning "touch the operating system"."""
    for risk in ("write", "exec"):
        assert effective_auto_approve(
            "bypass", "some_system_tool", _policy(risk=risk, sandbox="system")
        ) is False


def test_bypass_still_refuses_a_destructive_policy():
    assert effective_auto_approve(
        "bypass", "delete_file", _policy(risk="destructive", destructive=True)
    ) is False


# ── change classification ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "tool", ["knowledge_save", "obsidian_save", "todo_write", "knowledge_graph_ingest"]
)
def test_an_append_only_knowledge_write_classifies_as_additive(tool: str):
    """These never rewrite existing content, so they must not be reported as a
    mutation — a mutation is what triggers the overwrite confirmation."""
    result = classify_tool_call(tool, {}, policy=_policy(), path_exists=lambda _p: True)

    assert result["change_class"] == CHANGE_ADDITIVE
    assert result["reason"] == "adds new content only"


def test_a_write_over_an_existing_path_is_a_mutation_and_a_new_one_is_not():
    existing = classify_tool_call(
        "write_file", {"path": "notes.md"}, policy=_policy(),
        path_exists=lambda path: path == "notes.md",
    )
    fresh = classify_tool_call(
        "write_file", {"path": "new.md"}, policy=_policy(), path_exists=lambda _p: False,
    )

    assert existing["reason"] == "overwrites an existing file"
    assert fresh["reason"] == "creates a new file"


# ── the args-dependent policy override ───────────────────────────────────────
@pytest.mark.parametrize("prefix", LOCAL_WRITE_BLOCKED_PREFIXES)
def test_a_write_at_a_blocked_system_prefix_is_rewritten_destructive(prefix: str):
    """The table says ``write_file`` is an ordinary workspace write. Aimed at
    ``/etc`` it is not, and the registry — not the caller — is what says so.
    Every mode denies ``destructive``, so this rewrite is the whole guard."""
    registry = ToolRegistry(handlers={})
    target = f"{prefix.rstrip('/')}/hosts"

    policy = registry.policy_for("write_file", {"path": target})

    assert policy["destructive"] is True
    assert policy["risk"] == "destructive"
    assert policy["sandbox"] == "system"
    assert policy["auto_approve"] is False
    for mode in ("strict", "trusted", "bypass"):
        assert effective_auto_approve(mode, "write_file", dict(policy)) is False


def test_the_prefix_match_is_a_path_boundary_not_a_string_prefix():
    """``/etc-notes`` is a workspace file whose name starts with the blocked
    text; rewriting it would refuse an ordinary write."""
    registry = ToolRegistry(handlers={})

    assert registry.policy_for("write_file", {"path": "/etc-notes/plan.md"}) == dict(
        TOOL_GOVERNANCE["write_file"]
    )
    # A Windows-style separator is normalised before the comparison, so the
    # same rule holds however the caller spelled the path.
    assert registry.policy_for("write_file", {"path": "\\etc\\hosts"})["destructive"] is True


def test_an_untargeted_write_keeps_its_table_policy():
    registry = ToolRegistry(handlers={})

    assert registry.policy_for("write_file", {"path": "notes.md"}) == dict(
        TOOL_GOVERNANCE["write_file"]
    )
    assert registry.policy_for("write_file") == dict(TOOL_GOVERNANCE["write_file"])
