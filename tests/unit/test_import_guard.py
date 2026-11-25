import ast
import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
FORBIDDEN_IMPORTS = {"latticeai", "ltcai"}


def _has_forbidden_import(file_path: Path) -> bool:
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in FORBIDDEN_IMPORTS:
                return True
    return False


def test_lattice_brain_does_not_import_latticeai():
    brain_dir = PROJECT_ROOT / "lattice_brain"
    if not brain_dir.exists():
        pytest.skip("lattice_brain directory not present yet")
    violations = []
    for f in brain_dir.rglob("*.py"):
        if _has_forbidden_import(f):
            violations.append(str(f.relative_to(PROJECT_ROOT)))
    assert not violations, f"Found forbidden imports in lattice_brain: {violations}"


def test_no_direct_latticeai_import_in_potential_brain_paths():
    brain_dir = PROJECT_ROOT / "lattice_brain"
    if not brain_dir.exists():
        pytest.skip("lattice_brain directory not present yet")
    for f in brain_dir.rglob("*.py"):
        if _has_forbidden_import(f):
            pytest.fail(f"lattice_brain isolation violation: {f}")


def test_cli_runtime_helpers_import_smoke():
    from latticeai.cli.runtime import (
        _apply_extra_path,
        _has_module,
        _load_env_file,
    )

    assert callable(_load_env_file)
    assert callable(_apply_extra_path)
    assert callable(_has_module)


def test_load_env_file_does_not_overwrite_existing(monkeypatch, tmp_path):
    from latticeai.cli.runtime import _load_env_file

    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING_VAR=from_file\nNEW_VAR=from_file\n")
    monkeypatch.setenv("EXISTING_VAR", "already_set")
    _load_env_file(env_file)
    assert os.environ["EXISTING_VAR"] == "already_set"
    assert os.environ.get("NEW_VAR") == "from_file"


def test_load_env_file_loads_new_vars(tmp_path):
    from latticeai.cli.runtime import _load_env_file

    env_file = tmp_path / ".env.test"
    env_file.write_text("LATTICE_TEST_VAR=hello_cli\n")
    os.environ.pop("LATTICE_TEST_VAR", None)
    _load_env_file(env_file)
    assert os.environ["LATTICE_TEST_VAR"] == "hello_cli"
