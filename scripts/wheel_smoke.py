#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

WHEEL_MODULES = [
    "lattice_brain",
    "lattice_brain.embeddings",
    "lattice_brain.graph._kg_common",
    "lattice_brain.ingestion",
    "lattice_brain.multimodal",
    "lattice_brain.runtime",
    "latticeai",
    "latticeai.app_factory",
    "latticeai.worker_app",
    "latticeai.cli.entrypoint",
    "latticeai.api.worker_compute",
    "latticeai.api.worker_seams",
    "latticeai.core.embedding_providers",
    "latticeai.core.vector_index",
    "latticeai.models.router",
    "latticeai.runtime.build_phases.worker_profile",
    "latticeai.services.model_runtime",
    "latticeai.services.p_reinforce",
    "latticeai.tools",
    "latticeai.tools.knowledge",
]

REMOVED_ROOT_MODULES = [
    "server",
    "latticeai.server_app",
    "latticeai.api.chat",
    "latticeai.core.agent",
    "latticeai.integrations.telegram_bot",
    "latticeai.setup.wizard",
    "lattice_brain.graph.store",
    "lattice_brain.storage",
    "lattice_brain.workflow",
    "ltcai_cli",
    "auto_setup",
    "setup_wizard",
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
    + "".join(
        f"""
try:
    importlib.import_module({mod!r})
except ImportError:
    pass
else:
    raise AssertionError('removed root shim {mod} is still shipped in the wheel')
"""
        for mod in REMOVED_ROOT_MODULES
    )
    + f"print('wheel imports ok: {len(WHEEL_MODULES)} modules, {len(REMOVED_ROOT_MODULES)} shims gone')\n"
)

HEALTH_CHECK = """
from fastapi.testclient import TestClient
from latticeai.worker_app import create_worker_app

app = create_worker_app()
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
    import importlib.util

    if importlib.util.find_spec("build") is not None:
        run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
            cwd=REPO_ROOT,
        )
    else:
        print(
            "'build' package unavailable; falling back to pip wheel --no-deps",
            flush=True,
        )
        run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                str(REPO_ROOT),
                "--no-deps",
                "-w",
                str(out_dir),
            ],
            cwd=REPO_ROOT,
        )
    wheels = sorted(out_dir.glob("ltcai-*.whl"))
    if not wheels:
        raise SystemExit(f"no wheel produced in {out_dir}")
    return wheels[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel",
        type=Path,
        default=None,
        help="use an existing wheel instead of building",
    )
    parser.add_argument(
        "--skip-health",
        action="store_true",
        help="run import checks only (no app boot)",
    )
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

        work_dir = tmp_path / "non-repo-cwd"
        work_dir.mkdir()
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
