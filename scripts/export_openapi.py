#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema used by the desktop frontend client."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def main() -> int:
    from latticeai.app_factory import create_app

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "frontend/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LATTICEAI_REQUIRE_AUTH", "false")
    os.environ.setdefault("LATTICEAI_TUNNEL", "false")
    os.environ.setdefault("LATTICEAI_AUTOLOAD_MODELS", "false")
    app = create_app()
    schema = app.openapi()
    target.write_text(json.dumps(schema, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} with {len(schema.get('paths', {}))} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
