"""``LTCAI`` — start the AI-Worker this machine's Lattice host talks to.

This used to be the product's front door: it printed the LAN banner, ran a
``doctor`` dependency check, could open a Cloudflare tunnel, sent a Telegram
"server started" notification, and served ``server:app`` — the 464-route
FastAPI application.

v11.6.0 made ``lattice-host`` the product server (plan §설계 결정 1). The host
is what ``bin/ltcai.js`` starts, the host owns the banner and the network
posture, and the host is what *supervises this process*. So the entrypoint is
now one job: bind the worker profile on a loopback address and serve it.

Everything removed went somewhere real rather than away — the banner and the
reachability report to the host's own startup, ``doctor`` to ``GET /health``'s
access block (which the supervisor already polls), the tunnel to the network
boundary controls, the Telegram bridge to the bot's own process. What is left
would be a `uvicorn` invocation if it were not for the two lines above it: the
``.env`` file and ``LATTICEAI_EXTRA_PATH``, which a desktop bundle depends on
being applied *before* configuration is read.

The host normally launches the ASGI factory directly::

    python -m uvicorn latticeai.worker_app:create_worker_app --factory --host 127.0.0.1 --port N

and this command is the equivalent for a person debugging a worker by hand.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from latticeai.cli.runtime import _apply_extra_path, _load_env_file


def main() -> None:
    app_dir = Path(__file__).resolve().parent
    _load_env_file(app_dir / ".env")
    _apply_extra_path()

    parser = argparse.ArgumentParser(
        prog="LTCAI",
        description="Run the Lattice AI compute worker (the product server is lattice-host).",
    )
    parser.add_argument("--host", default=os.getenv("LATTICEAI_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("LATTICEAI_PORT") or "4825"))
    parser.add_argument(
        "--reload", action="store_true", help="Enable uvicorn reload for local development."
    )
    args = parser.parse_args()

    os.chdir(app_dir)
    # ``Config.from_env`` is the source of truth for the address the worker
    # binds and reports on ``/health``; the flags are written back so a
    # command-line override and the configuration cannot disagree.
    os.environ["LATTICEAI_HOST"] = str(args.host)
    os.environ["LATTICEAI_PORT"] = str(args.port)

    import uvicorn

    print(f"🧠 Lattice AI worker on http://{args.host}:{args.port}")
    uvicorn.run(
        "latticeai.worker_app:create_worker_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
