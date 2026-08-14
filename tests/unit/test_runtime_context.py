"""The build-phase order is a contract, so it gets a test rather than a comment.

WP-P1 cut the product's ten phases to the seven the worker still runs.
These tests pin three things:

1. the phase sequence itself,
2. which phase publishes which attribute,
3. that reading an attribute before its phase has run fails loudly.
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
    "phase_features",
]

# One representative attribute per phase. Not exhaustive on purpose: these are
# the ones whose producer moving would silently break a dependent phase.
PHASE_OWNERS = {
    "CONFIG": "config",
    "DATA_DIR": "config",
    "require_user": "identity",
    "load_users": "identity",
    "EMBEDDER": "brain",
    "model_router": "domain",
    "app": "web",
    "_spawn": "web",
    "model_service": "features",
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


def test_build_phase_order_is_fixed() -> None:
    assert [phase.__name__ for phase in BUILD_PHASES] == EXPECTED_PHASE_ORDER


def test_every_phase_is_documented_in_the_module_docstring() -> None:
    import latticeai.runtime.build_phases as module

    doc = module.__doc__ or ""
    for name in EXPECTED_PHASE_ORDER:
        label = name.removeprefix("phase_")
        assert f"``{label}``" in doc, f"{label} phase is not described in the docstring"


def test_domain_precedes_web_and_features_follows_it() -> None:
    """The two orderings that a naive split gets wrong."""
    order = [phase.__name__ for phase in BUILD_PHASES]
    assert order.index("phase_domain") < order.index("phase_web")
    assert order.index("phase_web") < order.index("phase_features")


def test_each_attribute_is_produced_by_its_declared_phase(tmp_path: Path) -> None:
    code = """
import json

from latticeai.app_factory import build_context

ctx = build_context()
print(json.dumps({
    "produced_by": dict(ctx._produced),
    "phases_run": list(dict.fromkeys(ctx._produced.values())),
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

    assert result["phases_run"] == [
        name.removeprefix("phase_") for name in EXPECTED_PHASE_ORDER
    ]
    assert result["name_count"] > 20, "the context lost most of the assembly state"


def test_require_names_the_unset_attribute() -> None:
    ctx = RuntimeContext()
    with pytest.raises(RuntimeError, match="EMBEDDER"):
        ctx.require("EMBEDDER")


def test_require_reports_the_producing_phase_when_known() -> None:
    ctx = RuntimeContext()
    ctx.enter("brain")
    ctx.set(EMBEDDER=object())
    del ctx.EMBEDDER
    with pytest.raises(RuntimeError, match="phase 'brain'"):
        ctx.require("EMBEDDER")


def test_unset_attribute_raises_rather_than_returning_none() -> None:
    """The failure mode a defaulted dataclass would have hidden."""
    ctx = RuntimeContext()
    with pytest.raises(AttributeError):
        _ = ctx.EMBEDDER


def test_set_records_the_current_phase() -> None:
    ctx = RuntimeContext()
    ctx.enter("config")
    ctx.set(CONFIG=object(), DATA_DIR=object())
    ctx.enter("identity")
    ctx.set(load_users=lambda: {})
    assert ctx._produced == {
        "CONFIG": "config",
        "DATA_DIR": "config",
        "load_users": "identity",
    }
    assert list(dict.fromkeys(ctx._produced.values())) == ["config", "identity"]


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


def test_build_is_an_orchestrator_not_an_assembly() -> None:
    """`build_context` must stay small; the phases hold the assembly."""
    import ast

    source = (REPO_ROOT / "latticeai" / "app_factory.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    build = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "build_context"
    )
    length = build.end_lineno - build.lineno + 1
    assert length < 60, f"build_context grew back to {length} lines — keep assembly in phases"


def test_build_phases_module_imports_nothing_heavy() -> None:
    """Phase modules must keep their heavy imports inside the functions."""
    import ast

    package_dir = REPO_ROOT / "latticeai" / "runtime" / "build_phases"
    sources = sorted(package_dir.glob("*.py"))
    assert len(sources) >= 2, "build_phases package lost its submodules"
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
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
            f"heavy import moved to module scope in {path.name}: "
            f"{module_level & forbidden}"
        )
