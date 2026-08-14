#!/usr/bin/env python3
"""Export the AI-Worker's OpenAPI schema — one input to the composed contract.

Until v11.6.0 this *was* the contract: it built the 464-route product app and
wrote ``frontend/openapi.json``. WP-P1 deleted that app. The committed contract
is unchanged (421 paths / 463 operations — clients still call all of them) but
its source is now ``scripts/compose_openapi.py``, which reassembles the
per-crate fragments in ``rust/fixtures/openapi/``.

What this script exports is the worker half, to a **scratch** path, so the
composer can check it: every operation the worker serves must already be in
``worker_keep.json`` (the gateway will not proxy a route with no contract), and
its ``info.version`` must agree with the fragments. It is an input, not the
output — see WP-I5 §"P1 cutover".

    node scripts/run_python.mjs scripts/export_openapi.py <scratch>/worker.json
    node scripts/run_python.mjs scripts/compose_openapi.py \
         --worker-spec <scratch>/worker.json --output frontend/openapi.json
"""

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
    # No default of ``frontend/openapi.json``: that file is the composer's
    # output now, and silently overwriting it with the 28-route worker spec is
    # exactly the mistake this argument exists to prevent.
    if len(sys.argv) < 2:
        print(
            "usage: export_openapi.py <output.json>\n"
            "  the committed contract is written by scripts/compose_openapi.py;\n"
            "  this exports the worker half to a scratch path it consumes.",
            file=sys.stderr,
        )
        return 2
    target = Path(sys.argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ltcai-openapi-") as temp_dir:
        with isolated_runtime_environment(Path(temp_dir)):
            # Import only after isolation is active in case a future dependency
            # starts reading environment or user paths during module import.
            from latticeai.worker_app import create_worker_app

            app = create_worker_app()
            schema = app.openapi()
    target.write_text(json.dumps(schema, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} with {len(schema.get('paths', {}))} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
