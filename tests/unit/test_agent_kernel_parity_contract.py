"""The Python half of the Python↔Rust **safety kernel** parity contract.

``rust/lattice-agent`` is pinned to the golden files under
``rust/fixtures/agent/golden/``. Those goldens were produced by the real Python
kernel, so a change to a Python gate can silently invalidate them: the Rust
tests keep passing (they compare against the same stale file), and nobody learns
that the port stopped being a port until an agent runs something it should not
have.

This module closes that loop from the other side. It re-runs the **real**
``is_circuit_breaker`` / ``effective_auto_approve`` / ``block_reason_for_tool``
/ ``classify_tool_call`` / ``run_command`` over the same grid, in the same
throwaway workspace, and asserts the goldens still describe what Python does.
Two consequences worth stating:

* it is a contract test, not a regression test — a deliberate policy change is
  *supposed* to fail it, and the fix is to regenerate with
  ``.venv/bin/python scripts/generate_agent_parity_fixtures.py`` and re-run
  ``cargo test -p lattice-agent`` so both halves move together;
* it imports nothing from the Rust side. The shared artefacts are JSON files;
  no toolchain is required to run this file.

Comparison is over the canonical JSON encoding rather than ``==`` so that a
``bool`` quietly becoming an ``int`` (``True == 1`` in Python) still fails.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_agent_parity_fixtures.py"


def _load_generator():
    """Import the fixture generator by path (``scripts`` is not a package)."""
    spec = importlib.util.spec_from_file_location("agent_parity_fixtures", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixtures = _load_generator()


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _golden(name: str) -> dict:
    path = fixtures.GOLDEN_DIR / name
    assert path.is_file(), f"{path} is missing — run {GENERATOR.name}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def workspace(tmp_path_factory) -> Path:
    """The throwaway ``AGENT_ROOT`` the command fixtures were generated in."""
    root = tmp_path_factory.mktemp("agent-kernel-parity") / "agent_workspace"
    fixtures.build_tree(root)
    return root


# ── structure ────────────────────────────────────────────────────────────────
def test_the_golden_directory_holds_exactly_what_the_generator_writes(workspace: Path):
    """A file added to the grid without regenerating shows up here, not as a
    confusing "missing fixture" panic from the cargo suite."""
    with fixtures.pinned_environment():
        expected = set(fixtures.build(workspace))
    assert {path.name for path in fixtures.GOLDEN_DIR.glob("*.json")} == expected


def test_the_manifest_still_describes_the_current_grid():
    manifest = _golden("manifest.json")
    assert manifest["modes"] == fixtures.MODES
    assert manifest["tools"] == fixtures.tool_universe()
    assert _canonical(manifest["arg_variants"]) == _canonical(fixtures.ARG_VARIANTS)
    assert _canonical(manifest["tree"]) == _canonical(fixtures.TREE)
    assert _canonical(manifest["constants"]) == _canonical(fixtures.constants())
    # The allow-listed binary is looked up on the fixed PATH and nowhere else.
    assert manifest["which_paths"] == [fixtures.command_tools._SAFE_EXECUTABLE_PATH]


def test_the_grid_is_wide_enough_to_be_worth_running():
    """Coverage here is the product of three axes; a shrunken axis is a silent
    loss of proof, so the floor is asserted rather than assumed."""
    assert len(fixtures.tool_universe()) >= 50
    assert len(fixtures.ARG_VARIANTS) >= 10
    assert len(fixtures.COMMAND_CASES) >= 50
    assert len(fixtures.SHLEX_CASES) >= 30
    calls = _golden("calls.json")["cases"]
    assert len(calls) == len(fixtures.tool_universe()) * len(fixtures.ARG_VARIANTS)
    assert len(calls) >= 500


def test_the_policy_table_is_the_real_registry():
    """The goldens are only worth anything if the policies in them are real."""
    from latticeai.core.tool_registry import TOOL_GOVERNANCE

    recorded = _golden("policies.json")
    assert _canonical(recorded) == _canonical(fixtures.policy_table())
    assert set(recorded["tools"]) == set(TOOL_GOVERNANCE)
    assert recorded["tools"]["run_command"]["risk"] == "exec"
    assert recorded["tools"]["read_file"]["auto_approve"] is True
    # The one args-dependent rewrite the registry performs must be in there.
    assert recorded["overrides"]["write_file|blocked_prefix"]["destructive"] is True


# ── decisions ────────────────────────────────────────────────────────────────
def test_the_call_golden_still_describes_the_python_breakers_and_classes():
    assert _canonical(_golden("calls.json")["cases"]) == _canonical(fixtures.call_rows())


@pytest.mark.parametrize("mode", fixtures.MODES)
def test_every_mode_still_decides_what_its_golden_records(mode: str):
    recorded = _golden(f"decisions__{mode}.json")
    assert recorded["mode"] == mode
    assert _canonical(recorded["cases"]) == _canonical(fixtures.decision_rows(mode))
    assert _canonical(recorded["change_class_cases"]) == _canonical(
        fixtures.change_class_rows(mode)
    )
    assert _canonical(recorded["plan_cases"]) == _canonical(fixtures.approval_rows(mode))


def test_mode_normalisation_and_the_contract_are_unchanged():
    assert _canonical(_golden("normalize.json")["cases"]) == _canonical(
        fixtures.normalize_rows()
    )
    assert _canonical(_golden("contract.json")) == _canonical(fixtures.contract_payload())


def test_unknown_mode_input_still_fails_closed():
    """The property behind the alias table, asserted on the recorded answers."""
    recorded = {
        _canonical(case["input"]): case["mode"]
        for case in _golden("normalize.json")["cases"]
    }
    for junk in ("", "   ", "junk", "strictly", "read-only", None, False, 0):
        assert recorded[_canonical(junk)] == "strict", junk
    assert recorded[_canonical("yolo")] == "bypass"
    assert recorded[_canonical("dangerously-skip-permissions")] == "bypass"


def test_circuit_breakers_are_mode_invariant_in_the_recorded_grid():
    """`bypass` skips prompts; it never unlocks a breaker. The goldens are laid
    out to make that checkable rather than merely stated: breakers live in the
    mode-independent file, and every blocked-by-breaker call is blocked in all
    three modes."""
    blocked = {
        (case["tool"], case["variant"])
        for case in _golden("calls.json")["cases"]
        if case["circuit_breaker"]
    }
    assert blocked, "the grid must contain calls that trip a breaker"
    for mode in fixtures.MODES:
        for case in _golden(f"decisions__{mode}.json")["cases"]:
            if (case["tool"], case["variant"]) in blocked:
                assert case["block_reason"], f"{mode}/{case['tool']}/{case['variant']}"
                assert case["block_reason"].startswith("BLOCKED: ")


# ── command sandbox ──────────────────────────────────────────────────────────
def test_the_shlex_golden_still_describes_python_shlex():
    assert _canonical(_golden("shlex.json")["cases"]) == _canonical(fixtures.shlex_rows())


def test_the_command_validator_still_returns_the_recorded_verdicts(workspace: Path):
    recorded = _golden("commands.json")
    with fixtures.pinned_environment():
        rows, spawn_env, searched = fixtures.command_rows(workspace)
    assert _canonical(rows) == _canonical(recorded["cases"])
    assert _canonical(spawn_env) == _canonical(recorded["spawn_env"])
    assert set(searched) == {fixtures.command_tools._SAFE_EXECUTABLE_PATH}


def test_the_sandboxed_environment_is_a_replacement(workspace: Path):
    """Four variables, a fixed PATH, and the workspace as HOME. Anything else
    the parent holds — tokens, keys, ssh agents — must not reach the child."""
    env = _golden("commands.json")["spawn_env"]
    assert sorted(env) == ["HOME", "LANG", "LC_ALL", "PATH"]
    assert env["HOME"] == "<AGENT_ROOT>"
    assert env["PATH"] == fixtures.command_tools._SAFE_EXECUTABLE_PATH
    assert "." not in env["PATH"].split(":")


def test_the_file_sandbox_still_refuses_the_same_paths(workspace: Path):
    assert _canonical(_golden("paths.json")["cases"]) == _canonical(
        fixtures.path_rows(workspace)
    )


def test_execution_still_produces_the_recorded_bytes(workspace: Path):
    with fixtures.pinned_environment():
        rows = fixtures.execution_rows(workspace)
    assert _canonical(rows) == _canonical(_golden("execution.json")["cases"])


def test_the_output_cap_is_a_tail_slice(workspace: Path):
    """12,000 characters of a 24,000 character file, and it is the *last*
    12,000 — a port that kept the head would look right until it mattered."""
    from latticeai.tools import MAX_COMMAND_OUTPUT

    recorded = {case["key"]: case for case in _golden("execution.json")["cases"]}
    stdout = recorded["cat_truncates"]["stdout"]
    assert len(stdout) == MAX_COMMAND_OUTPUT == 12_000
    assert stdout.startswith("0001500\n")
    assert stdout.endswith("0002999\n")
