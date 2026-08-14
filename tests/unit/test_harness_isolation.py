"""Regression guards for state-safe validation harnesses."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_exporter():
    path = ROOT / "scripts" / "export_openapi.py"
    spec = importlib.util.spec_from_file_location("export_openapi", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_openapi_export_environment_is_disposable_and_restored(tmp_path, monkeypatch):
    exporter = _load_exporter()
    original_home = str(tmp_path / "real-home")
    original_data = str(tmp_path / "real-data")
    monkeypatch.setenv("HOME", original_home)
    monkeypatch.setenv("LATTICEAI_DATA_DIR", original_data)

    sandbox = tmp_path / "sandbox"
    with exporter.isolated_runtime_environment(sandbox) as env:
        assert env["HOME"] == str(sandbox / "home")
        assert env["LATTICEAI_DATA_DIR"] == str(sandbox / "data")
        assert env["LATTICEAI_BRAIN_DIR"] == str(sandbox / "brain")
        assert env["LATTICEAI_AGENT_ROOT"] == str(sandbox / "agent-workspace")
        assert env["LATTICEAI_OBSIDIAN_VAULT_DIR"] == str(sandbox / "vault")
        assert env["LATTICEAI_STORAGE_ENGINE"] == "sqlite"
        assert env["LATTICEAI_POSTGRES_DSN"] == ""
        assert env["LATTICEAI_AUTOLOAD_MODELS"] == "false"
        for key in (
            "HOME",
            "LATTICEAI_DATA_DIR",
            "LATTICEAI_BRAIN_DIR",
            "LATTICEAI_AGENT_ROOT",
            "LATTICEAI_OBSIDIAN_VAULT_DIR",
        ):
            assert Path(env[key]).is_dir()

    assert exporter.os.environ["HOME"] == original_home
    assert exporter.os.environ["LATTICEAI_DATA_DIR"] == original_data


def test_integration_runner_and_ci_use_disposable_state():
    runner = (ROOT / "scripts" / "run_integration_tests.mjs").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "mkdtempSync" in runner
    assert "rmSync(sandboxRoot, { recursive: true, force: true })" in runner
    assert "process.env.LTCAI_TEST_BASE_URL ||" not in runner
    for key in (
        "HOME",
        "LATTICEAI_DATA_DIR",
        "LATTICEAI_BRAIN_DIR",
        "LATTICEAI_AGENT_ROOT",
        "LATTICEAI_OBSIDIAN_VAULT_DIR",
        "LATTICEAI_STORAGE_ENGINE",
        "LATTICEAI_POSTGRES_DSN",
    ):
        assert f"{key}:" in runner

    assert "run: npm run test:integration" in workflow
    assert "python -m uvicorn server:app" not in workflow


def test_launchers_never_pin_the_guardless_module_form():
    """`ltcai` and start_ai.sh must not pin a worker command that serves nothing.

    `latticeai/worker_app.py` exports `main()` but has no
    `if __name__ == "__main__"` guard, so `python -m latticeai.worker_app`
    imports and exits 0 without binding: the supervisor restarts it forever and
    the front door 502s. Pinning the uvicorn string instead is not the fix —
    `LATTICEAI_DESKTOP_BACKEND_CMD` is the supervisor's rule 1, whose args are
    passed verbatim, and the worker port is chosen at runtime. Both launchers
    name the *interpreter* (`LTCAI_PYTHON`) and let the supervisor build its own
    command (rust/lattice-host/src/supervisor/command.rs::WORKER_FACTORY).
    """
    for name in ("bin/ltcai.js", "start_ai.sh"):
        launcher = (ROOT / name).read_text(encoding="utf-8")
        assert "LTCAI_PYTHON" in launcher, name
        assert 'LATTICEAI_DESKTOP_BACKEND_CMD = `' not in launcher, name
        assert 'export LATTICEAI_DESKTOP_BACKEND_CMD="' not in launcher, name


def test_one_door_runners_pin_an_absolute_worker_interpreter():
    """Both live-server harnesses must hand `lattice-host` a real path.

    `LTCAI_PYTHON` reaches the supervisor, which spawns the worker with `PATH`
    **prefixed** by `/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin`
    (rust/lattice-host/src/supervisor/worker_env.rs). A bare `python3` is
    therefore looked up against a different `PATH` than the runner validated it
    on: on GitHub Actions the interpreter `pip install -e "."` populated lives
    under /opt/hostedtoolcache, but the child found /usr/bin/python3 first and
    died with "No module named uvicorn" — the worker exit 1 that turned every
    proxied route into a 502. Resolving to `sys.executable` is what keeps the
    interpreter the same one on both sides of the process boundary.

    The second half is the diagnostics: the worker's stderr goes to
    `$HOME/.ltcai/desktop-sidecar.err.log`, and both runners rewrite `HOME`
    into a sandbox they delete on the way out, so a dead worker took its only
    explanation with it and CI showed nothing but a timeout.
    """
    for name in ("run_integration_tests.mjs", "run_sidecar_e2e.mjs"):
        runner = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "import sys; print(sys.executable)" in runner, name
        assert "LTCAI_PYTHON: python," in runner, name
        assert "desktop-sidecar.err.log" in runner, name
        assert "dumpWorkerLogs()" in runner, name
        # The front door is the binary, not a Python ASGI app: what these
        # runners spawn is whatever resolveHostBinary() returned.
        assert "const hostBinary = resolveHostBinary();" in runner, name
        assert "spawn(\n    hostBinary," in runner, name
