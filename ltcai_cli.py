"""Command line entrypoint for Lattice AI."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from pathlib import Path


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def doctor() -> int:
    checks = [
        ("Python 3.11+", sys.version_info >= (3, 11), sys.version.split()[0], True),
        ("FastAPI", _has_module("fastapi"), "required server dependency", True),
        ("Uvicorn", _has_module("uvicorn"), "required server dependency", True),
        ("OpenAI SDK", _has_module("openai"), "required for cloud providers", False),
        ("MLX", _has_module("mlx"), "required for Apple Silicon local models", False),
        ("MLX-LM", _has_module("mlx_lm"), "required for local text models", False),
        ("MLX-VLM", _has_module("mlx_vlm"), "required for Gemma/VLM models", False),
        ("Ollama binary", shutil.which("ollama") is not None, "optional local-server engine", False),
    ]
    data_dir = Path(os.getenv("LATTICEAI_DATA_DIR") or Path.home() / ".ltcai")
    static_dir = Path(os.getenv("LATTICEAI_STATIC_DIR") or Path(__file__).resolve().parent / "static")
    checks.extend([
        ("Data dir", data_dir.exists() or data_dir.parent.exists(), str(data_dir), True),
        ("Static UI", static_dir.exists(), str(static_dir), True),
    ])

    ok = True
    for label, passed, detail, required in checks:
        icon = "OK" if passed else ("MISS" if required else "OPTIONAL")
        print(f"[{icon}] {label}: {detail}")
        ok = ok and (passed or not required)

    cloud_keys = ["OPENAI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY"]
    configured = [key for key in cloud_keys if os.getenv(key)]
    print(f"[INFO] Cloud keys configured: {', '.join(configured) if configured else 'none'}")
    return 0 if ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="LTCAI", description="Run the Lattice AI local server.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="Check local runtime dependencies and configuration.")
    parser.add_argument("--host", default=os.getenv("LATTICEAI_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("LATTICEAI_PORT") or "4825"))
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload for local development.")
    args = parser.parse_args()

    if args.command == "doctor":
        raise SystemExit(doctor())

    app_dir = Path(__file__).resolve().parent
    os.chdir(app_dir)

    import uvicorn

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
