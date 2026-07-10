#!/usr/bin/env python3
"""Export the FastAPI OpenAPI schema used by the desktop frontend client."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


@contextmanager
def isolated_runtime_environment(root: Path) -> Iterator[Mapping[str, str]]:
    """Point every runtime/user-state path at a disposable directory.

    OpenAPI generation constructs the real application so route wiring stays
    authoritative. That construction must not inspect or mutate the caller's
    HOME, Brain, keyring, agent workspace, or local Lattice data.
    """

    home = root / "home"
    paths = {
        "HOME": home,
        "USERPROFILE": home,
        "XDG_CACHE_HOME": root / "cache",
        "XDG_CONFIG_HOME": root / "config",
        "XDG_DATA_HOME": root / "share",
        "TMPDIR": root / "tmp",
        "TEMP": root / "tmp",
        "TMP": root / "tmp",
        "LATTICEAI_DATA_DIR": root / "data",
        "LATTICEAI_BRAIN_DIR": root / "brain",
        "LATTICEAI_AGENT_ROOT": root / "agent-workspace",
        "LATTICEAI_OBSIDIAN_VAULT_DIR": root / "vault",
        "LATTICEAI_STATIC_DIR": root / "static",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    overrides = {
        **{key: str(value) for key, value in paths.items()},
        "LATTICEAI_MODE": "local",
        "LATTICEAI_STORAGE_ENGINE": "sqlite",
        "LATTICEAI_POSTGRES_DSN": "",
        "LATTICEAI_REQUIRE_AUTH": "false",
        "LATTICEAI_TUNNEL": "false",
        "LATTICEAI_ENABLE_TELEGRAM": "false",
        "LATTICEAI_AUTOLOAD_MODELS": "false",
        "LATTICEAI_ALLOW_MODEL_DOWNLOADS": "false",
        "LATTICEAI_AUTO_READ_CHAT_PATHS": "false",
        "LATTICEAI_DISCORD_PERMISSION_WEBHOOK": "",
        "LATTICEAI_DISCORD_BOT_TOKEN": "",
        "OIDC_DISCOVERY_URL": "",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield overrides
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "frontend/openapi.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ltcai-openapi-") as temp_dir:
        with isolated_runtime_environment(Path(temp_dir)):
            # Import only after isolation is active in case a future dependency
            # starts reading environment or user paths during module import.
            from latticeai.app_factory import create_app

            app = create_app()
            schema = app.openapi()
    target.write_text(json.dumps(schema, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} with {len(schema.get('paths', {}))} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
