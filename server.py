"""Thin compatibility entrypoint for the Lattice AI FastAPI app.

The application assembly and route wiring live in ``latticeai.server_app``.
This module keeps the historical ``server:app`` import path used by uvicorn,
Docker, CLI scripts, and older tests.
"""

from __future__ import annotations

from latticeai import server_app as _server_app


for _name in dir(_server_app):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_server_app, _name)


app = _server_app.app


def main() -> None:
    _server_app.main()


if __name__ == "__main__":
    main()
