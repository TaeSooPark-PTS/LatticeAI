"""Explicit-consent Docker setup wizard for Postgres/pgvector."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class DockerPostgresPlan:
    compose_path: Path
    project_name: str
    service_name: str = "postgres"
    port: int = 5432

    def command(self) -> List[str]:
        return [
            "docker",
            "compose",
            "-p",
            self.project_name,
            "-f",
            str(self.compose_path),
            "up",
            "-d",
            self.service_name,
        ]


class DockerPostgresWizard:
    """Creates and starts a local Postgres container only after consent."""

    def __init__(self, data_dir: Path, *, port: int = 5432) -> None:
        self.data_dir = Path(data_dir)
        self.port = int(port)

    def write_compose(self) -> DockerPostgresPlan:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        compose = self.data_dir / "postgres.compose.yml"
        compose.write_text(
            f"""services:
  postgres:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    environment:
      POSTGRES_DB: lattice_brain
      POSTGRES_USER: lattice
      POSTGRES_PASSWORD: lattice-local-only
    ports:
      - "127.0.0.1:{self.port}:5432"
    volumes:
      - ./postgres-data:/var/lib/postgresql/data
""",
            encoding="utf-8",
        )
        return DockerPostgresPlan(
            compose_path=compose,
            project_name="lattice-brain",
            port=self.port,
        )

    def start(
        self,
        *,
        consent: bool,
        dry_run: bool = False,
        runner=subprocess.run,
    ) -> Dict[str, object]:
        plan = self.write_compose()
        if not consent:
            return {
                "status": "consent_required",
                "started": False,
                "compose_path": str(plan.compose_path),
                "command": plan.command(),
            }
        if dry_run:
            return {
                "status": "dry_run",
                "started": False,
                "compose_path": str(plan.compose_path),
                "command": plan.command(),
            }
        completed = runner(plan.command(), check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            return {
                "status": "failed",
                "started": False,
                "compose_path": str(plan.compose_path),
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        return {
            "status": "started",
            "started": True,
            "compose_path": str(plan.compose_path),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }


__all__ = ["DockerPostgresPlan", "DockerPostgresWizard"]
