"""lattice_brain independence guarantees (v4.4.0 physical extraction).

Two contracts, each enforced in a clean subprocess so the result cannot be
polluted by other tests that already imported ``latticeai``:

1. Importing every ``lattice_brain`` module never imports ``latticeai``.
2. The keep-set compute surface is usable in isolation — embedder, chunking,
   multi-modal facts, the ingestion probe — without FastAPI.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

GUARD = """
import importlib.abc
import sys

class LatticeaiBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "latticeai" or fullname.startswith("latticeai."):
            raise ImportError(
                "ISOLATION VIOLATION: lattice_brain pulled in %s" % fullname
            )
        return None

sys.meta_path.insert(0, LatticeaiBlocker())
"""


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", GUARD + textwrap.dedent(code)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_lattice_brain_never_imports_latticeai():
    """Fails if any lattice_brain module imports latticeai."""
    proc = _run(
        """
        import importlib
        import pkgutil
        import sys

        import lattice_brain

        violations = []
        for mod in pkgutil.walk_packages(lattice_brain.__path__, "lattice_brain."):
            try:
                importlib.import_module(mod.name)
            except ImportError as exc:
                if "ISOLATION VIOLATION" in str(exc):
                    violations.append((mod.name, str(exc)))
                else:
                    pass

        for name in lattice_brain.__all__:
            if name != "__version__":
                getattr(lattice_brain, name)

        assert not violations, violations
        leaked = [m for m in sys.modules if m == "latticeai" or m.startswith("latticeai.")]
        assert not leaked, "latticeai leaked into sys.modules: %s" % leaked
        print("ISOLATION_OK")
        """
    )
    assert proc.returncode == 0, proc.stderr
    assert "ISOLATION_OK" in proc.stdout


def test_lattice_brain_usable_in_isolation(tmp_path):
    """Exercises the remaining compute surface with latticeai imports blocked."""
    proc = _run(
        f"""
        import sys
        from pathlib import Path

        data_dir = Path({str(tmp_path)!r}) / "brain"
        data_dir.mkdir(parents=True, exist_ok=True)

        from lattice_brain import LocalEmbeddingModel, extract_image_facts
        from lattice_brain.graph._kg_common.text import chunk_strategy_for
        from lattice_brain.ingestion.pipeline import IngestionPipeline

        model = LocalEmbeddingModel()
        vector = model.embed("isolation note")
        assert len(vector) > 0

        strategy = chunk_strategy_for("note.md", content_type="text/plain")
        assert strategy

        pipe = IngestionPipeline()
        status = pipe.multimodal_status()
        assert isinstance(status, dict)

        leaked = [m for m in sys.modules if m == "latticeai" or m.startswith("latticeai.")]
        assert not leaked, leaked
        print("USABLE_OK")
        """
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "USABLE_OK" in proc.stdout
