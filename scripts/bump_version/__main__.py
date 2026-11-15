"""Module entrypoint for ``python -m scripts.bump_version``."""

from __future__ import annotations

from scripts.bump_version.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
