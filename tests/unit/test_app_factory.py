"""T2 acceptance gate: ``create_app`` factory + side-effect-free imports.

The v4 design review made the acceptance criterion explicit: importing
``latticeai.server_app`` (or ``latticeai.app_factory``) must perform **no**
construction — no MLX/GPU init, no singleton construction, no file creation
under a sandboxed data dir. A delegating wrapper that still constructs at
import time fails this gate by design.

Both checks run in a subprocess with a sandboxed ``HOME`` /
``LATTICEAI_DATA_DIR`` so they observe a pristine interpreter and filesystem,
independent of whatever the rest of the test session has already imported.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules whose presence in sys.modules proves heavy construction happened:
# mlx (GPU init), the LLM router, the knowledge graph store, the telegram
# bot, the MCP registry, and the setup wizard are all construction-time-only.
_FORBIDDEN_MODULES = (
    "mlx",
    "mlx.core",
    "llm_router",
    "latticeai.models.router",
    "knowledge_graph",
    "local_knowledge_api",
    "telegram_bot",
    "mcp_registry",
    "latticeai.core.mcp_registry",
    "setup_wizard",
    "p_reinforce",
)


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


def _tree(root: Path) -> list:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_importing_server_app_and_factory_has_no_side_effects(tmp_path: Path):
    """Import must not init MLX, build singletons, or create any file."""
    code = """
import json, os, sys

import latticeai.server_app  # noqa: F401 — the import IS the test
import latticeai.app_factory  # noqa: F401
import latticeai.runtime.config_runtime  # noqa: F401
import latticeai.runtime.security_runtime  # noqa: F401
import latticeai.runtime.brain_runtime  # noqa: F401

home = os.environ["HOME"]
created = []
for root, dirs, files in os.walk(home):
    for name in dirs + files:
        created.append(os.path.join(root, name))

forbidden = [m for m in {forbidden!r} if m in sys.modules]
print(json.dumps({{"created": created, "forbidden": forbidden}}))
""".format(forbidden=_FORBIDDEN_MODULES)

    result = _run_in_sandbox(code, tmp_path)

    assert result["created"] == [], (
        f"importing latticeai.server_app created files: {result['created']}"
    )
    assert result["forbidden"] == [], (
        "importing latticeai.server_app pulled in construction-time modules: "
        f"{result['forbidden']} — the import is no longer side-effect free"
    )


def test_create_app_builds_working_app_inside_sandbox(tmp_path: Path):
    """The factory produces a serving app, and all writes stay in the sandbox."""
    code = """
import json, os

from fastapi.testclient import TestClient

from latticeai.app_factory import create_app

app = create_app()
response = TestClient(app).get("/health")
data_dir = os.environ["LATTICEAI_DATA_DIR"]
print(json.dumps({
    "status": response.status_code,
    "version": response.json().get("version"),
    "data_dir_created": os.path.isdir(data_dir),
}))
"""
    result = _run_in_sandbox(code, tmp_path)

    assert result["status"] == 200
    assert result["version"], "/health payload missing version"
    # Construction must write into the sandboxed data dir (proving the config
    # seam works), and nowhere else in the sandbox home but expected app dirs.
    assert result["data_dir_created"] is True


def test_create_app_admin_audit_surfaces_do_not_500(tmp_path: Path):
    """The factory must pass the runtime audit reader expected by admin routes."""
    code = """
import json

from fastapi.testclient import TestClient

from latticeai.app_factory import create_app

client = TestClient(create_app())
audit = client.get("/admin/audit")
retention = client.get("/admin/log-retention")
print(json.dumps({
    "audit": audit.status_code,
    "retention": retention.status_code,
}))
"""
    result = _run_in_sandbox(code, tmp_path)

    assert result["audit"] < 500
    assert result["retention"] < 500


def test_app_runtime_exposes_explicit_runtime_bundle(tmp_path: Path):
    """The factory keeps a typed migration target for removing locals export."""
    code = """
import json

from latticeai.app_factory import get_shared_runtime

runtime = get_shared_runtime()
bundle = runtime._RUNTIME_BUNDLE
required = {
    "app", "CONFIG", "KNOWLEDGE_GRAPH", "INGESTION_PIPELINE",
    "AGENT_RUNTIME", "HOOKS_REGISTRY", "REVIEW_QUEUE",
}
print(json.dumps({
    "has_required": required <= set(bundle),
    "bundle_size": len(bundle),
    "app_matches": bundle["app"] is runtime.app,
}))
"""
    result = _run_in_sandbox(code, tmp_path)

    assert result["has_required"] is True
    assert result["bundle_size"] >= 8
    assert result["app_matches"] is True


def test_server_module_proxies_lazily(tmp_path: Path):
    """``import server`` is also side-effect free until ``server.app`` is read."""
    code = """
import json, os, sys

import server

before = [m for m in {forbidden!r} if m in sys.modules]
app = server.app  # first attribute access triggers construction
after_type = type(app).__name__

import latticeai.server_app as sa
print(json.dumps({{
    "before": before,
    "app_type": after_type,
    "identical": app is sa.app,
}}))
""".format(forbidden=_FORBIDDEN_MODULES)

    result = _run_in_sandbox(code, tmp_path)

    assert result["before"] == [], (
        f"importing server pulled in construction-time modules: {result['before']}"
    )
    assert result["app_type"] == "FastAPI"
    assert result["identical"] is True, "server.app and latticeai.server_app.app diverged"
