"""AST-based import guard tests for v6.1 isolation boundaries.

Ensures lattice_brain (and future extracted cores) never imports latticeai/ltcai.
"""
import ast
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
FORBIDDEN_IMPORTS = {"latticeai", "ltcai"}


def _has_forbidden_import(file_path: Path) -> bool:
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except Exception:
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
    """lattice_brain core must remain free of latticeai/ltcai imports for safe extraction."""
    brain_dir = PROJECT_ROOT / "lattice_brain"
    if not brain_dir.exists():
        pytest.skip("lattice_brain directory not present yet")
    violations = []
    for f in brain_dir.rglob("*.py"):
        if _has_forbidden_import(f):
            violations.append(str(f.relative_to(PROJECT_ROOT)))
    assert not violations, f"Found forbidden imports in lattice_brain: {violations}"


def test_no_direct_latticeai_import_in_potential_brain_paths():
    """Explicit guard: full AST scan of lattice_brain/ directory."""
    brain_dir = PROJECT_ROOT / "lattice_brain"
    if not brain_dir.exists():
        pytest.skip("lattice_brain directory not present yet")
    for f in brain_dir.rglob("*.py"):
        if _has_forbidden_import(f):
            pytest.fail(f"lattice_brain isolation violation: {f}")
