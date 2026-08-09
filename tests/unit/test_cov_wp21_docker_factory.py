"""wp21 coverage — the Docker/Postgres consent wizard and the storage factory.

Docker is not available on the coverage leg and starting a container from a
test would be neither deterministic nor welcome, so ``start()`` is driven
through the ``runner`` seam the wizard already exposes. The three post-consent
outcomes (dry run, non-zero exit, success) are asserted on the exact argv the
wizard would hand to ``subprocess.run``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.storage.base import StorageUnavailable
from lattice_brain.storage.docker import DockerPostgresWizard
from lattice_brain.storage.factory import storage_from_env
from lattice_brain.storage.postgres import PostgresEngine
from lattice_brain.storage.sqlite import SQLiteEngine


class _RecordingRunner:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self._result = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._result


def _refuse(*args, **kwargs):
    raise AssertionError("docker must not be invoked on this path")


# ── docker wizard ────────────────────────────────────────────────────────────


def test_compose_file_binds_pgvector_to_loopback_only(tmp_path: Path):
    wizard = DockerPostgresWizard(tmp_path / "pg", port=55432)

    plan = wizard.write_compose()

    compose = plan.compose_path.read_text(encoding="utf-8")
    assert plan.compose_path == tmp_path / "pg" / "postgres.compose.yml"
    assert "image: pgvector/pgvector:pg16" in compose
    assert '- "127.0.0.1:55432:5432"' in compose
    assert plan.port == 55432
    assert plan.project_name == "lattice-brain"
    assert plan.command() == [
        "docker",
        "compose",
        "-p",
        "lattice-brain",
        "-f",
        str(plan.compose_path),
        "up",
        "-d",
        "postgres",
    ]


def test_start_dry_run_shows_the_command_without_running_it(tmp_path: Path):
    wizard = DockerPostgresWizard(tmp_path / "pg")

    result = wizard.start(consent=True, dry_run=True, runner=_refuse)

    compose = tmp_path / "pg" / "postgres.compose.yml"
    assert result == {
        "status": "dry_run",
        "started": False,
        "compose_path": str(compose),
        "command": wizard.write_compose().command(),
    }
    assert compose.exists(), "a dry run still writes the compose file the operator will read"


def test_start_surfaces_a_docker_failure_verbatim(tmp_path: Path):
    runner = _RecordingRunner(
        returncode=125,
        stdout="Pulling postgres",
        stderr="Cannot connect to the Docker daemon",
    )
    wizard = DockerPostgresWizard(tmp_path / "pg", port=5544)

    result = wizard.start(consent=True, runner=runner)

    assert result["status"] == "failed"
    assert result["started"] is False
    assert result["returncode"] == 125
    assert result["stdout"] == "Pulling postgres"
    assert result["stderr"] == "Cannot connect to the Docker daemon"
    assert "command" not in result
    (args, kwargs), = runner.calls
    assert args[0] == wizard.write_compose().command()
    assert kwargs == {"check": False, "capture_output": True, "text": True}


def test_start_reports_a_started_container_after_consent(tmp_path: Path):
    runner = _RecordingRunner(stdout="Container lattice-brain-postgres-1 Started", stderr="")
    wizard = DockerPostgresWizard(tmp_path / "pg")

    result = wizard.start(consent=True, runner=runner)

    assert result == {
        "status": "started",
        "started": True,
        "compose_path": str(tmp_path / "pg" / "postgres.compose.yml"),
        "stdout": "Container lattice-brain-postgres-1 Started",
        "stderr": "",
    }
    assert len(runner.calls) == 1


def test_start_defaults_to_subprocess_run_but_never_reaches_it_without_consent(tmp_path: Path):
    """The default runner is the real one; consent is what keeps it unused."""
    default_runner = DockerPostgresWizard.start.__kwdefaults__["runner"]
    assert (default_runner.__module__, default_runner.__name__) == (
        subprocess.run.__module__,
        subprocess.run.__name__,
    )

    result = DockerPostgresWizard(tmp_path / "pg").start(consent=False)

    assert result["status"] == "consent_required"
    assert result["started"] is False
    assert result["command"][0] == "docker"


# ── storage factory ──────────────────────────────────────────────────────────


def test_empty_or_missing_engine_selects_local_sqlite(tmp_path: Path):
    default = storage_from_env({}, data_dir=tmp_path)
    blank = storage_from_env({"LATTICEAI_STORAGE_ENGINE": "  "}, data_dir=tmp_path)

    assert isinstance(default, SQLiteEngine)
    assert default.db_path == tmp_path / "knowledge_graph.sqlite"
    assert isinstance(blank, SQLiteEngine)


@pytest.mark.parametrize("alias", ["postgres", "pg", "pgvector", "  PostGres  "])
def test_postgres_aliases_build_an_engine_with_the_default_schema(alias: str, tmp_path: Path):
    engine = storage_from_env(
        {
            "LATTICEAI_STORAGE_ENGINE": alias,
            "LATTICEAI_POSTGRES_DSN": "postgresql://example.invalid/brain",
            "LATTICEAI_POSTGRES_SCHEMA": "",
        },
        data_dir=tmp_path,
    )

    assert isinstance(engine, PostgresEngine)
    assert engine.config.dsn == "postgresql://example.invalid/brain"
    assert engine.config.schema == "lattice_brain"


def test_postgres_schema_override_is_honoured(tmp_path: Path):
    engine = storage_from_env(
        {
            "LATTICEAI_STORAGE_ENGINE": "pgvector",
            "LATTICEAI_POSTGRES_DSN": "postgresql://example.invalid/brain",
            "LATTICEAI_POSTGRES_SCHEMA": "brain_scale",
        },
        data_dir=tmp_path,
    )

    assert engine.config.schema == "brain_scale"


def test_explicit_postgres_without_a_dsn_never_silently_falls_back(tmp_path: Path):
    with pytest.raises(StorageUnavailable, match="SQLite fallback is disabled"):
        storage_from_env({"LATTICEAI_STORAGE_ENGINE": "postgres"}, data_dir=tmp_path)


def test_an_unknown_engine_name_is_refused_by_name(tmp_path: Path):
    with pytest.raises(StorageUnavailable, match="Unknown Brain Core storage engine: mysql"):
        storage_from_env({"LATTICEAI_STORAGE_ENGINE": "MySQL"}, data_dir=tmp_path)
