#!/usr/bin/env python3
"""Installed-wheel smoke test.

Builds the wheel, installs it into a *fresh* venv, and verifies — from a
non-repo working directory — that every module the wheel ships actually
imports and that the FastAPI app boots and answers ``/health``. This kills the
class of "works only with ``pip install -e .`` from the repo root" failures
(e.g. the v3.x wheels that omitted the root ``setup.py`` wizard module while
``latticeai.server_app`` imported it).

Usage:
    python scripts/wheel_smoke.py                 # build + install + import + /health
    python scripts/wheel_smoke.py --wheel dist/ltcai-X.Y.Z-py3-none-any.whl
    python scripts/wheel_smoke.py --skip-health   # imports only

The build step prefers ``python -m build --wheel`` and falls back to
``pip wheel . --no-deps`` when the ``build`` package is unavailable.
Exit code is non-zero on any failure so CI can gate on it.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every importable module the wheel ships (pyproject py-modules + packages).
WHEEL_MODULES = [
    "setup_wizard",
    "latticeai",
    "latticeai.server_app",
    "latticeai.app_factory",
    "latticeai.models.router",
    "latticeai.core.mcp_registry",
    "latticeai.api.knowledge_graph",
    "ltcai_cli",
    "auto_setup",
    "server",
    "mcp_registry",
    "kg_schema",
    "knowledge_graph",
    "knowledge_graph_api",
    "local_knowledge_api",
    "llm_router",
    "p_reinforce",
    "telegram_bot",
    "tools",
]

IMPORT_CHECK = (
    "import importlib\n"
    + "".join(f"importlib.import_module({mod!r})\n" for mod in WHEEL_MODULES)
    + f"print('wheel imports ok: {len(WHEEL_MODULES)} modules')\n"
)

HEALTH_CHECK = """
from fastapi.testclient import TestClient
from latticeai.app_factory import create_app

app = create_app()
response = TestClient(app).get("/health")
assert response.status_code == 200, f"/health returned {response.status_code}"
payload = response.json()
assert "version" in payload, f"/health payload missing version: {payload}"
print("health ok:", payload.get("version"))
"""


def run(cmd: list[str], **kwargs) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def build_wheel(out_dir: Path) -> Path:
    try:
        import build  # noqa: F401 — probe only
        run([sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)], cwd=REPO_ROOT)
    except ImportError:
        print("'build' package unavailable; falling back to pip wheel --no-deps", flush=True)
        run(
            [sys.executable, "-m", "pip", "wheel", str(REPO_ROOT), "--no-deps", "-w", str(out_dir)],
            cwd=REPO_ROOT,
        )
    wheels = sorted(out_dir.glob("ltcai-*.whl"))
    if not wheels:
        raise SystemExit(f"no wheel produced in {out_dir}")
    return wheels[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, default=None, help="use an existing wheel instead of building")
    parser.add_argument("--skip-health", action="store_true", help="run import checks only (no app boot)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="ltcai-wheel-smoke-") as tmp:
        tmp_path = Path(tmp)

        wheel = args.wheel.resolve() if args.wheel else build_wheel(tmp_path / "dist")
        print(f"wheel under test: {wheel}", flush=True)

        venv_dir = tmp_path / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
        bin_dir = "Scripts" if os.name == "nt" else "bin"
        python = venv_dir / bin_dir / ("python.exe" if os.name == "nt" else "python")

        run([str(python), "-m", "pip", "install", "--quiet", str(wheel)])

        # A cwd that is NOT the repo: imports must resolve from site-packages,
        # never from checkout-relative files.
        work_dir = tmp_path / "non-repo-cwd"
        work_dir.mkdir()
        # Sandbox all user-data writes so the smoke test never touches ~/.ltcai.
        env = {
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "LATTICEAI_DATA_DIR": str(tmp_path / "home" / ".ltcai"),
            "LATTICEAI_AGENT_ROOT": str(tmp_path / "home" / "agent_workspace"),
            "LATTICEAI_BRAIN_DIR": str(tmp_path / "home" / ".ltcai-brain"),
            "LATTICEAI_ENABLE_TELEGRAM": "false",
            "LATTICEAI_AUTOLOAD_MODELS": "false",
            "PYTHONPATH": "",
        }
        (tmp_path / "home").mkdir()

        run([str(python), "-c", IMPORT_CHECK], cwd=work_dir, env=env)

        if not args.skip_health:
            run([str(python), "-c", HEALTH_CHECK], cwd=work_dir, env=env)

    print("wheel smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
