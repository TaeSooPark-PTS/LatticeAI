"""The build-phase order is a contract, so it gets a test rather than a comment.

``app_factory._build`` used to be one 1,300-line function. Splitting it into
phases is only safe while the dependency order holds: ``phase_web`` needs the
model router from ``phase_domain``, ``phase_services`` needs handles that
``phase_web`` produced, and several closures resolve dependencies that a
*later* phase publishes.

These tests pin three things:

1. the phase sequence itself,
2. which phase publishes which attribute (so a reordering that breaks a
   dependency fails here, not in production),
3. that reading an attribute before its phase has run fails loudly.

The full assembly runs in a subprocess with a sandboxed HOME, the same way
``test_app_factory.py`` does, so it never touches the developer's real data
directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from latticeai.runtime.build_phases import BUILD_PHASES
from latticeai.runtime.runtime_context import RuntimeContext

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_PHASE_ORDER = [
    "phase_platform",
    "phase_config",
    "phase_identity",
    "phase_brain",
    "phase_domain",
    "phase_web",
    "phase_services",
    "phase_foundation_routes",
    "phase_platform_features",
    "phase_interaction",
]

# One representative attribute per phase. Not exhaustive on purpose: these are
# the ones whose producer moving would silently break a dependent phase.
PHASE_OWNERS = {
    "CONFIG": "config",
    "DATA_DIR": "config",
    "append_audit_event": "identity",
    "require_user": "identity",
    "load_users": "identity",
    "KNOWLEDGE_GRAPH": "brain",
    "WORKSPACE_OS": "brain",
    "save_to_history": "brain",
    "get_history": "brain",
    "model_router": "domain",
    "gardener": "domain",
    "CHAT_SERVICE": "domain",
    "app": "web",
    "_spawn": "web",
    "ui_file_response": "web",
    "_graph_stats_safe": "web",
    "SEARCH_SERVICE": "services",
    "app_context": "services",
    "CHAT_AGENT_RUNTIME": "services",
    "PLATFORM": "platform_features",
    "REVIEW_QUEUE": "platform_features",
    "RUN_EXECUTOR": "platform_features",
    "model_runtime": "interaction",
}


def _sandbox_env(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    home.mkdir()
    return {
        **os.environ,
        "HOME": str(home),
        "LATTICEAI_DATA_DIR": str(home / ".ltcai"),
        "LATTICEAI_AGENT_ROOT": str(home / "agent_workspace"),
        "LATTICEAI_BRAIN_DIR": str(home / ".ltcai-brain"),
        "LATTICEAI_ENABLE_TELEGRAM": "false",
        "LATTICEAI_AUTOLOAD_MODELS": "false",
        "PYTHONPATH": str(REPO_ROOT),
    }


def _run_in_sandbox(code: str, tmp_path: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_sandbox_env(tmp_path),
        timeout=180,
    )
    assert proc.returncode == 0, f"subprocess failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(proc.stdout.splitlines()[-1])


# ── contract 1: the sequence ─────────────────────────────────────────────────
def test_build_phase_order_is_fixed() -> None:
    assert [phase.__name__ for phase in BUILD_PHASES] == EXPECTED_PHASE_ORDER


def test_every_phase_is_documented_in_the_module_docstring() -> None:
    import latticeai.runtime.build_phases as module

    doc = module.__doc__ or ""
    for name in EXPECTED_PHASE_ORDER:
        label = name.removeprefix("phase_")
        assert f"``{label}``" in doc, f"{label} phase is not described in the docstring"


def test_domain_precedes_web_and_services_follows_it() -> None:
    """The two orderings that a naive split gets wrong."""
    order = [phase.__name__ for phase in BUILD_PHASES]
    # phase_web wires the lifespan, static status routes and model runtime
    # against the router that phase_domain constructs.
    assert order.index("phase_domain") < order.index("phase_web")
    # The AppContext carries ui_file_response / local_sysinfo / graph_stats,
    # all produced by phase_web.
    assert order.index("phase_web") < order.index("phase_services")
    # Routers can only mount once the AppContext exists.
    assert order.index("phase_services") < order.index("phase_foundation_routes")


# ── contract 2: who produces what ────────────────────────────────────────────
def test_each_attribute_is_produced_by_its_declared_phase(tmp_path: Path) -> None:
    code = """
import json

from latticeai.app_factory import build_context

ctx = build_context()
print(json.dumps({
    "produced_by": ctx.produced_by,
    "phases_run": ctx.phases_run(),
    "name_count": len(ctx.names()),
}))
"""
    result = _run_in_sandbox(code, tmp_path)

    produced = result["produced_by"]
    mismatched = {
        name: (produced.get(name), expected)
        for name, expected in PHASE_OWNERS.items()
        if produced.get(name) != expected
    }
    assert mismatched == {}, f"attribute moved phase: {mismatched}"

    # foundation_routes only mounts routers, so it publishes nothing.
    assert result["phases_run"] == [
        label
        for label in (name.removeprefix("phase_") for name in EXPECTED_PHASE_ORDER)
        if label != "foundation_routes"
    ]
    assert result["name_count"] > 100, "the context lost most of the assembly state"


# ── contract 3: reading too early fails loudly ───────────────────────────────
def test_require_names_the_unset_attribute() -> None:
    ctx = RuntimeContext()
    with pytest.raises(RuntimeError, match="KNOWLEDGE_GRAPH"):
        ctx.require("KNOWLEDGE_GRAPH")


def test_require_reports_the_producing_phase_when_known() -> None:
    ctx = RuntimeContext()
    ctx.enter("brain")
    ctx.set(KNOWLEDGE_GRAPH=object())
    del ctx.KNOWLEDGE_GRAPH
    with pytest.raises(RuntimeError, match="phase 'brain'"):
        ctx.require("KNOWLEDGE_GRAPH")


def test_unset_attribute_raises_rather_than_returning_none() -> None:
    """The failure mode a defaulted dataclass would have hidden."""
    ctx = RuntimeContext()
    with pytest.raises(AttributeError):
        _ = ctx.WORKSPACE_OS


def test_set_records_the_current_phase() -> None:
    ctx = RuntimeContext()
    ctx.enter("config")
    ctx.set(CONFIG=object(), DATA_DIR=object())
    ctx.enter("identity")
    ctx.set(load_users=lambda: {})
    assert ctx.produced_by == {
        "CONFIG": "config",
        "DATA_DIR": "config",
        "load_users": "identity",
    }
    assert ctx.phases_run() == ["config", "identity"]


def test_adopt_copies_named_keys_from_a_stage_mapping() -> None:
    ctx = RuntimeContext()
    ctx.enter("config")
    ctx.adopt({"CONFIG": 1, "DATA_DIR": 2, "unused": 3}, "CONFIG", "DATA_DIR")
    assert ctx.CONFIG == 1
    assert ctx.DATA_DIR == 2
    assert "unused" not in ctx.names()


def test_config_argument_is_carried_for_the_config_phase() -> None:
    sentinel = object()
    assert RuntimeContext(sentinel).config_arg is sentinel


# ── the decomposition itself ─────────────────────────────────────────────────
def test_build_is_an_orchestrator_not_an_assembly() -> None:
    """`_build` must stay small; the phases hold the assembly."""
    import ast

    source = (REPO_ROOT / "latticeai" / "app_factory.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    build = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_build"
    )
    length = build.end_lineno - build.lineno + 1
    assert length < 60, f"_build grew back to {length} lines — keep assembly in phases"


def test_build_phases_module_imports_nothing_heavy() -> None:
    """Phase modules must keep their heavy imports inside the functions."""
    import ast

    source = (
        REPO_ROOT / "latticeai" / "runtime" / "build_phases.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_level = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [])
    } | {
        node.module.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden = {"mlx", "fastapi", "uvicorn", "keyring"}
    assert module_level & forbidden == set(), (
        f"heavy import moved to module scope: {module_level & forbidden}"
    )
