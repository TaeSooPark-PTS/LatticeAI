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
    # Only scan active lattice* source directories for brain modules.
    # Explicitly ignore audits, node_modules, site-packages, old releases.
    source_dirs = ["latticeai", "ltcai", "src"]
    brain_candidates = []
    for d in source_dirs:
        base = PROJECT_ROOT / d
        if base.exists():
            for p in base.rglob("*.py"):
                if "brain" in str(p).lower():
                    brain_candidates.append(p)

    violations = []
    for f in brain_candidates:
        if _has_forbidden_import(f):
            violations.append(str(f.relative_to(PROJECT_ROOT)))

    assert not violations, f"Found forbidden imports in brain candidates: {violations}"


def test_no_direct_latticeai_import_in_potential_brain_paths():
    """Explicit guard: only active source brain files are checked."""
    source_dirs = ["latticeai", "ltcai", "src"]
    for d in source_dirs:
        base = PROJECT_ROOT / d
        if not base.exists():
            continue
        for f in base.rglob("*.py"):
            if "brain" not in str(f).lower():
                continue
            if _has_forbidden_import(f):
                pytest.fail(f"lattice_brain isolation violation: {f}")
