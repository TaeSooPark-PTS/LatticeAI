"""Brain Core package extraction guard.

``lattice_brain`` is being prepared for extraction as a standalone package.
The one structural rule that makes that possible: the package must never
import ``latticeai`` (the FastAPI/product layer imports the Brain, never the
other way around). This test walks every module's AST so a violation fails CI
before it becomes a hidden runtime coupling.
"""

from __future__ import annotations

import ast
from pathlib import Path

BRAIN_ROOT = Path(__file__).resolve().parents[2] / "lattice_brain"

FORBIDDEN_PREFIXES = ("latticeai",)


def _imported_modules(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (level > 0) stay inside the package by definition.
            if node.level == 0 and node.module:
                yield node.module, node.lineno


def test_lattice_brain_never_imports_latticeai():
    assert BRAIN_ROOT.is_dir(), "lattice_brain package must exist"
    violations = []
    for path in sorted(BRAIN_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module, lineno in _imported_modules(tree):
            if module.split(".")[0].startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.relative_to(BRAIN_ROOT.parent)}:{lineno} imports {module}")
    assert not violations, (
        "lattice_brain must stay importable without the product layer "
        "(Brain Core extraction rule):\n" + "\n".join(violations)
    )


def test_lattice_brain_declares_version_and_public_surface():
    import lattice_brain

    assert lattice_brain.__version__
    # The lazy public surface must stay resolvable — a stale _LAZY entry would
    # break the standalone package at import time for downstream users.
    for name in ("LocalEmbeddingModel", "MultimodalPorts", "extract_image_facts"):
        assert getattr(lattice_brain, name) is not None
