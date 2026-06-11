"""Thin compatibility entrypoint for the Lattice AI FastAPI app.

The application is built by ``latticeai.app_factory.create_app``; this module
keeps the historical ``server:app`` import path used by uvicorn, Docker, CLI
scripts, and older tests. Attribute access is proxied lazily so that simply
importing ``server`` performs no construction — ``uvicorn server:app`` (or
``from server import app``) triggers the factory on first access.
"""

from __future__ import annotations

from typing import Any, List

from latticeai import server_app as _server_app


def __getattr__(name: str) -> Any:
    return getattr(_server_app, name)


def __dir__() -> List[str]:
    return sorted(set(globals()) | set(dir(_server_app)))


def main() -> None:
    _server_app.main()


if __name__ == "__main__":
    main()
