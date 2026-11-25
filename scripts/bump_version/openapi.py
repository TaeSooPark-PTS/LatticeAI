from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

OPENAPI_FRAGMENTS: Tuple[str, ...] = (
    "admin",
    "auth",
    "chat",
    "knowledge_search",
    "mcp_market",
    "memory_brain",
    "models_misc",
    "portability_network",
    "review_proposals",
    "static_ui",
    "ui_redirects",
    "worker_keep",
    "workspace",
)

OPENAPI_TARGETS: List[Tuple[str, str, Optional[str]]] = [
    ("frontend/openapi.json", "openapi-spec", None),
    ("rust/fixtures/openapi/_envelope.json", "openapi-envelope", None),
    *[
        (f"rust/fixtures/openapi/{family}.json", "openapi-fragment", None)
        for family in OPENAPI_FRAGMENTS
    ],
    ("rust/fixtures/openapi/_index.json", "openapi-index", None),
]

DEFAULT_REPO = Path(__file__).resolve().parents[2]
REPO = DEFAULT_REPO


def canonical_openapi_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


_canonical_openapi_json = canonical_openapi_json


def apply_openapi_spec(text: str, version: str) -> Tuple[bool, str, str, bool]:
    data = json.loads(text)
    info = data.get("info") if isinstance(data.get("info"), dict) else None
    ok = info is not None and "version" in info
    if not ok or info is None:
        return False, "", text, False
    current = str(info["version"])
    changed = current != version
    info["version"] = version
    new = canonical_openapi_json(data)
    return True, current, new, changed


def apply_openapi_envelope(text: str, version: str) -> Tuple[bool, str, str, bool]:
    data = json.loads(text)
    spec_obj = data.get("spec") if isinstance(data.get("spec"), dict) else None
    info = (
        spec_obj.get("info")
        if isinstance(spec_obj, dict) and isinstance(spec_obj.get("info"), dict)
        else None
    )
    ok = "spec_version" in data and info is not None and "version" in info
    if not ok or info is None:
        return False, "", text, False
    sv = str(data["spec_version"])
    iv = str(info["version"])
    current = sv if sv != version else iv
    changed = sv != version or iv != version
    data["spec_version"] = version
    info["version"] = version
    new = canonical_openapi_json(data)
    return True, current, new, changed


def apply_openapi_fragment(text: str, version: str) -> Tuple[bool, str, str, bool]:
    data = json.loads(text)
    ok = "spec_version" in data
    if not ok:
        return False, "", text, False
    current = str(data["spec_version"])
    changed = current != version
    data["spec_version"] = version
    new = canonical_openapi_json(data)
    return True, current, new, changed


def apply_openapi_index(
    text: str,
    version: str,
    index_path: Path,
    *,
    check: bool = False,
) -> Tuple[bool, str, str, bool]:
    data = json.loads(text)
    ok = "spec_version" in data
    if not ok:
        return False, "", text, False
    current = str(data["spec_version"])
    data["spec_version"] = version
    if not check:
        env_meta = data.get("envelope")
        if isinstance(env_meta, dict) and "file" in env_meta:
            env_path = index_path.parent / str(env_meta["file"])
            if env_path.is_file():
                env_meta["sha256"] = hashlib.sha256(env_path.read_bytes()).hexdigest()
        for entry in data.get("fragments", []):
            if isinstance(entry, dict) and "file" in entry:
                frag_path = index_path.parent / str(entry["file"])
                if frag_path.is_file():
                    entry["sha256"] = hashlib.sha256(frag_path.read_bytes()).hexdigest()
    new = canonical_openapi_json(data)
    changed = current != version or new != text
    return True, current, new, changed


def bump_openapi(
    version: str,
    *,
    check: bool = False,
    repo: Optional[Path] = None,
) -> int:
    from scripts.bump_version.targets import is_valid_semver, resolve_repo, run_targets

    if not is_valid_semver(version):
        print(f"error: '{version}' is not a sane semantic version", file=sys.stderr)
        return 2
    target_repo = resolve_repo(repo)
    return run_targets(OPENAPI_TARGETS, version, check=check, repo=target_repo)
