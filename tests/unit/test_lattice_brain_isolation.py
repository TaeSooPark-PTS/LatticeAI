"""lattice_brain independence guarantees (v4.4.0 physical extraction).

Two contracts, each enforced in a clean subprocess so the result cannot be
polluted by other tests that already imported ``latticeai``:

1. Importing every ``lattice_brain`` module never imports ``latticeai``;
   an import-hook makes any attempt fail loudly instead of silently passing.
2. ``lattice_brain`` is fully usable in isolation — graph ingest/search,
   conversations, context assembly, workflow runs, the agent runtime, and an
   encrypted ``.latticebrain`` archive round-trip — without FastAPI and
   without the ``latticeai`` package.
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
    """Fails if any lattice_brain module — eagerly, or lazily through the
    package facade — imports latticeai."""
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
                    # optional third-party deps may be absent; the latticeai
                    # boundary is the only thing this test polices
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
    """Exercises the Brain Core end-to-end with latticeai imports blocked and
    FastAPI never started."""
    proc = _run(
        f"""
        import sys
        from pathlib import Path

        data_dir = Path({str(tmp_path)!r}) / "brain"
        data_dir.mkdir(parents=True, exist_ok=True)

        from lattice_brain import BrainCore, BrainCoreConfig

        core = BrainCore(BrainCoreConfig(data_dir=data_dir))

        # Ingestion -> knowledge graph (unified pipeline, no FastAPI hooks app)
        from lattice_brain.ingestion import IngestionItem, IngestionPipeline

        pipe = IngestionPipeline(core.knowledge)
        res = pipe.ingest(
            IngestionItem(
                source_type="note",
                title="Isolation note",
                text="lattice_brain runs standalone. The knowledge graph is durable.",
                owner="iso@test.local",
            ),
            user_email="iso@test.local",
        )
        assert res.status == "ok", res.status
        stats = core.knowledge.stats()
        assert sum(stats["nodes"].values()) >= 1, stats

        # Graph search
        hits = core.knowledge.search("standalone")
        assert isinstance(hits, dict)

        # Conversations
        core.conversations.append({{
            "role": "user",
            "content": "hello isolated brain",
            "timestamp": "2026-06-13T00:00:00",
            "user_email": "iso@test.local",
            "conversation_id": "conv-iso",
        }})
        assert core.conversations.count() == 1

        # Context assembly (seam-injected, no app context)
        from lattice_brain.context import ContextAssembler

        assembled = ContextAssembler().assemble("standalone")
        assert assembled.text is not None and assembled.trace() is not None

        # Workflow engine
        from lattice_brain.workflow import WorkflowEngine

        wf = {{
            "name": "iso",
            "nodes": [
                {{"id": "t", "type": "trigger", "config": {{"trigger": "manual"}}, "next": "a"}},
                {{"id": "a", "type": "tool", "name": "noop", "config": {{"tool": "noop"}}, "next": "o"}},
                {{"id": "o", "type": "output", "config": {{}}, "next": None}},
            ],
        }}
        run = WorkflowEngine({{"tool": lambda node, context: {{"ran": node["id"]}}}}).run(wf)
        assert run.status == "ok", run.status

        # Agent runtime + hooks live inside the package
        from lattice_brain.runtime.agent_runtime import AgentRuntime
        from lattice_brain.runtime.hooks import HooksRegistry, dispatch_tool

        assert AgentRuntime is not None and HooksRegistry is not None
        assert callable(dispatch_tool)

        # Encrypted .latticebrain archive round-trip
        out = data_dir / "iso.latticebrain"
        core.archive.create(out, passphrase="iso-pass-123")
        assert out.exists() and out.stat().st_size > 0
        info = core.archive.inspect(out, passphrase="iso-pass-123")
        assert info
        verdict = core.archive.verify(out, passphrase="iso-pass-123")
        assert verdict

        leaked = [m for m in sys.modules if m == "latticeai" or m.startswith("latticeai.")]
        assert not leaked, leaked
        print("USABLE_OK")
        """
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "USABLE_OK" in proc.stdout
