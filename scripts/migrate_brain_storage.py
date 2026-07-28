#!/usr/bin/env python3
"""Brain storage migration utility.

Examples:
    python scripts/migrate_brain_storage.py plan --sqlite ~/.ltcai/knowledge_graph.sqlite --dsn postgresql://...
    python scripts/migrate_brain_storage.py migrate --sqlite ~/.ltcai/knowledge_graph.sqlite --dsn postgresql://...
    python scripts/migrate_brain_storage.py docker-plan --data-dir ~/.ltcai/postgres
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lattice_brain.storage import (
    DockerPostgresWizard,
    PostgresEngine,
    SQLiteToPostgresMigrator,
)


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("plan", "migrate"):
        p = sub.add_parser(name)
        p.add_argument("--sqlite", type=Path, required=True)
        p.add_argument("--dsn", required=True)
        p.add_argument("--schema", default="lattice_brain")

    docker = sub.add_parser("docker-plan")
    docker.add_argument("--data-dir", type=Path, required=True)
    docker.add_argument("--port", type=int, default=5432)

    args = parser.parse_args()
    if args.command == "docker-plan":
        wizard = DockerPostgresWizard(args.data_dir, port=args.port)
        result = wizard.start(consent=False)
        _print_json(result)
        return 0

    migrator = SQLiteToPostgresMigrator(
        args.sqlite,
        PostgresEngine(args.dsn, schema=args.schema),
    )
    _print_json(migrator.migrate(dry_run=args.command == "plan"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
