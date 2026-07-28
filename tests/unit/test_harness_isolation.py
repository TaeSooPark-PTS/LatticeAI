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
