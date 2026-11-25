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
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("LATTICEAI_PORT") or "4825")
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn reload for local development.",
    )
    args = parser.parse_args()

    os.chdir(app_dir)
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
