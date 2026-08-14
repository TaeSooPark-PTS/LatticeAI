"""T2 acceptance gate: ``create_worker_app`` factory + side-effect-free imports.

Importing ``latticeai.worker_app`` or ``latticeai.app_factory`` must perform
**no** construction. The factory produces a serving worker app whose writes
stay in the sandbox.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_FORBIDDEN_MODULES = (
    "mlx",
    "mlx.core",
    "latticeai.models.router",
    "lattice_brain.graph.store",
    "latticeai.services.local_knowledge",
    "latticeai.integrations.telegram_bot",
    "latticeai.core.mcp_registry",
    "latticeai.setup.wizard",
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


def test_importing_worker_app_and_factory_has_no_side_effects(tmp_path: Path):
    """Import must not init MLX, build singletons, or create any file."""
    code = """
import json, os, sys

import latticeai.worker_app  # noqa: F401 — the import IS the test
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
        f"importing latticeai.worker_app created files: {result['created']}"
    )
    assert result["forbidden"] == [], (
        "importing latticeai.worker_app pulled in construction-time modules: "
        f"{result['forbidden']} — the import is no longer side-effect free"
    )


def test_create_worker_app_builds_working_app_inside_sandbox(tmp_path: Path):
    """The factory produces a serving worker, and all writes stay in the sandbox."""
    code = """
import json, os

from fastapi.testclient import TestClient

from latticeai.worker_app import create_worker_app

app = create_worker_app()
response = TestClient(app).get("/health")
data_dir = os.environ["LATTICEAI_DATA_DIR"]
print(json.dumps({
    "status": response.status_code,
    "version": response.json().get("version"),
    "data_dir_created": os.path.isdir(data_dir),
    "data_dir_mode": os.stat(data_dir).st_mode & 0o777,
}))
"""
    result = _run_in_sandbox(code, tmp_path)

    assert result["status"] == 200
    assert result["version"], "/health payload missing version"
    assert result["data_dir_created"] is True
    if os.name == "posix":
        assert result["data_dir_mode"] == 0o700


def test_worker_app_does_not_serve_product_admin_routes(tmp_path: Path):
    """The product admin surface is native; a worker must 404 it."""
    code = """
import json

from fastapi.testclient import TestClient

from latticeai.worker_app import create_worker_app

client = TestClient(create_worker_app())
audit = client.get("/admin/audit")
briefing = client.get("/api/command/briefing")
print(json.dumps({
    "audit": audit.status_code,
    "briefing": briefing.status_code,
}))
"""
    result = _run_in_sandbox(code, tmp_path)

    assert result["audit"] == 404
    assert result["briefing"] == 404


def test_build_context_exposes_the_worker_runtime(tmp_path: Path):
    """The typed context is the migration target; there is no AppRuntime."""
    code = """
import json

from latticeai.app_factory import build_context

ctx = build_context()
print(json.dumps({
    "has_app": hasattr(ctx, "app"),
    "has_config": hasattr(ctx, "CONFIG"),
    "has_embedder": hasattr(ctx, "EMBEDDER"),
    "app_type": type(ctx.app).__name__,
    "name_count": len(ctx.names()),
}))
"""
    result = _run_in_sandbox(code, tmp_path)

    assert result["has_app"] is True
    assert result["has_config"] is True
    assert result["has_embedder"] is True
    assert result["app_type"] == "FastAPI"
    assert result["name_count"] > 20


def test_factory_has_no_ambient_namespace_export():
    source = (REPO_ROOT / "latticeai" / "app_factory.py").read_text(encoding="utf-8")

    assert "build_runtime_namespace(locals" not in source
    assert "dict(locals())" not in source
    assert "def create_app" not in source
