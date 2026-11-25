#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "frontend" / "openapi.json"
MAPPING_PATH = REPO_ROOT / "scripts" / "openapi_route_families.json"
FRAGMENT_DIR = REPO_ROOT / "rust" / "fixtures" / "openapi"

ENVELOPE_NAME = "_envelope.json"
INDEX_NAME = "_index.json"
WORKER_FAMILY = "worker_keep"
GENERATOR = "scripts/gen_openapi_fragments.py"

SERIALIZATION = {
    "encoder": "json.dumps",
    "sort_keys": True,
    "indent": 2,
    "ensure_ascii": True,
    "trailing_newline": True,
}

SCHEMA_REF_PREFIX = "#/components/schemas/"


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_spec(path: Path = SPEC_PATH) -> Dict[str, Any]:
    spec: Dict[str, Any] = load_json(path)
    return spec


def load_mapping(path: Path = MAPPING_PATH) -> Dict[str, Any]:
    mapping: Dict[str, Any] = load_json(path)
    return mapping


def operation_key(method: str, path: str) -> str:
    return f"{method.upper()} {path}"


def spec_operations(spec: Mapping[str, Any]) -> List[Tuple[str, str, str]]:
    found: List[Tuple[str, str, str]] = []
    for path, item in spec["paths"].items():
        for method in item:
            found.append((operation_key(method, path), path, method))
    return found


def schema_refs(node: Any) -> Set[str]:
    names: Set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if (
                key == "$ref"
                and isinstance(value, str)
                and value.startswith(SCHEMA_REF_PREFIX)
            ):
                names.add(value[len(SCHEMA_REF_PREFIX) :])
            else:
                names.update(schema_refs(value))
    elif isinstance(node, list):
        for value in node:
            names.update(schema_refs(value))
    return names


def schema_closure(seeds: Iterable[str], schemas: Mapping[str, Any]) -> Set[str]:
    reached: Set[str] = set()
    pending = list(seeds)
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        pending.extend(schema_refs(schemas.get(name, {})) - reached)
    return reached


def family_of(key: str, mapping: Mapping[str, Any]) -> str:
    entry = mapping["operations"].get(key)
    if entry is None:
        raise KeyError(
            f"{key} is not in {MAPPING_PATH.name}. Every operation in "
            f"{SPEC_PATH.name} needs a family — add it there first."
        )
    family: str = entry["family"]
    return family


def split_spec(
    spec: Mapping[str, Any], mapping: Mapping[str, Any]
) -> Dict[str, Dict[str, Any]]:
    schemas = spec["components"]["schemas"]
    families: Dict[str, Dict[str, Any]] = {}
    for name, meta in mapping["families"].items():
        families[name] = {
            "family": name,
            "wp": meta["wp"],
            "target": meta["target"],
            "summary": meta["summary"],
            "generated_by": GENERATOR,
            "source": SPEC_PATH.relative_to(REPO_ROOT).as_posix(),
            "spec_version": spec["info"]["version"],
            "path_order": [],
            "operation_order": [],
            "greedy_path_params": {},
            "paths": {},
            "components": {"schemas": {}},
        }
    greedy: Mapping[str, str] = mapping.get("greedy_path_params", {})

    seeds: Dict[str, Set[str]] = {name: set() for name in families}
    for key, path, method in spec_operations(spec):
        name = family_of(key, mapping)
        fragment = families[name]
        if path not in fragment["paths"]:
            fragment["paths"][path] = {}
            fragment["path_order"].append(path)
        fragment["paths"][path][method] = spec["paths"][path][method]
        fragment["operation_order"].append(key)
        if key in greedy:
            fragment["greedy_path_params"][key] = greedy[key]
        seeds[name].update(schema_refs(spec["paths"][path][method]))

    for name, fragment in families.items():
        for schema_name in sorted(schema_closure(seeds[name], schemas)):
            fragment["components"]["schemas"][schema_name] = schemas[schema_name]
        fragment["path_count"] = len(fragment["paths"])
        fragment["operation_count"] = len(fragment["operation_order"])
        fragment["schema_count"] = len(fragment["components"]["schemas"])
    return families


def build_envelope(spec: Mapping[str, Any], attributed: Set[str]) -> Dict[str, Any]:
    schemas = spec["components"]["schemas"]
    unattributed = sorted(set(schemas) - attributed)
    skeleton: Dict[str, Any] = {
        key: value for key, value in spec.items() if key not in ("paths", "components")
    }
    components = {
        key: value for key, value in spec["components"].items() if key != "schemas"
    }
    components["schemas"] = {name: schemas[name] for name in unattributed}
    skeleton["components"] = components
    return {
        "family": "_envelope",
        "generated_by": GENERATOR,
        "source": SPEC_PATH.relative_to(REPO_ROOT).as_posix(),
        "spec_version": spec["info"]["version"],
        "note": [
            "The non-path half of the contract: openapi version, info, and any",
            "component that no operation references. scripts/compose_openapi.py",
            "starts from this skeleton and merges every fragment's paths and",
            "schemas into it.",
        ],
        "unattributed_schemas": unattributed,
        "spec": skeleton,
    }


def build_index(
    spec: Mapping[str, Any],
    fragments: Mapping[str, Dict[str, Any]],
    envelope: Mapping[str, Any],
) -> Dict[str, Any]:
    entries = []
    for name in sorted(fragments):
        fragment = fragments[name]
        entries.append(
            {
                "family": name,
                "file": f"{name}.json",
                "paths": fragment["path_count"],
                "operations": fragment["operation_count"],
                "schemas": fragment["schema_count"],
                "sha256": sha256_text(canonical_json(fragment)),
            }
        )
    return {
        "generated_by": GENERATOR,
        "source": SPEC_PATH.relative_to(REPO_ROOT).as_posix(),
        "spec_version": spec["info"]["version"],
        "note": [
            "Committed manifest for the per-family OpenAPI fragments.",
            "'serialization' is the encoding frontend/openapi.json is written",
            "with; composition reproduces the committed bytes only because the",
            "composer uses the same one.",
            "Regenerate with: .venv/bin/python scripts/gen_openapi_fragments.py",
        ],
        "serialization": dict(SERIALIZATION),
        "totals": {
            "paths": len(spec["paths"]),
            "operations": sum(entry["operations"] for entry in entries),
            "schemas": len(spec["components"]["schemas"]),
            "families": len(entries),
        },
        "envelope": {
            "file": ENVELOPE_NAME,
            "unattributed_schemas": len(envelope["unattributed_schemas"]),
            "sha256": sha256_text(canonical_json(envelope)),
        },
        "fragments": entries,
    }


def render_all(spec: Mapping[str, Any], mapping: Mapping[str, Any]) -> Dict[str, str]:
    fragments = split_spec(spec, mapping)
    attributed: Set[str] = set()
    for fragment in fragments.values():
        attributed.update(fragment["components"]["schemas"])
    envelope = build_envelope(spec, attributed)
    index = build_index(spec, fragments, envelope)
    rendered = {
        f"{name}.json": canonical_json(fragment) for name, fragment in fragments.items()
    }
    rendered[ENVELOPE_NAME] = canonical_json(envelope)
    rendered[INDEX_NAME] = canonical_json(index)
    return rendered


def write_fragments(directory: Path, rendered: Mapping[str, str]) -> List[str]:
    directory.mkdir(parents=True, exist_ok=True)
    changed: List[str] = []
    for name in sorted(rendered):
        target = directory / name
        text = rendered[name]
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            changed.append(name)
        target.write_text(text, encoding="utf-8")
    stale = sorted(
        path.name for path in directory.glob("*.json") if path.name not in rendered
    )
    for name in stale:
        (directory / name).unlink()
        changed.append(name)
    return changed


def check_fragments(directory: Path, rendered: Mapping[str, str]) -> List[str]:
    problems: List[str] = []
    for name in sorted(rendered):
        target = directory / name
        if not target.exists():
            problems.append(f"missing: {name}")
        elif target.read_text(encoding="utf-8") != rendered[name]:
            problems.append(f"stale: {name}")
    for path in sorted(directory.glob("*.json")):
        if path.name not in rendered:
            problems.append(f"unexpected: {path.name}")
    return problems


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate OpenAPI fragment files.")
    parser.add_argument(
        "--spec", type=Path, default=SPEC_PATH, help="committed contract"
    )
    parser.add_argument(
        "--mapping", type=Path, default=MAPPING_PATH, help="route → family map"
    )
    parser.add_argument(
        "--out", type=Path, default=FRAGMENT_DIR, help="fragment directory"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when the committed fragments are stale",
    )
    args = parser.parse_args(argv)

    spec = load_spec(args.spec)
    mapping = load_mapping(args.mapping)
    rendered = render_all(spec, mapping)

    if args.check:
        problems = check_fragments(args.out, rendered)
        for problem in problems:
            print(f"  {problem}")
        if problems:
            print(f"{len(problems)} fragment(s) out of date — run {GENERATOR}")
            return 1
        print(f"fragments up to date ({len(rendered)} files in {args.out})")
        return 0

    changed = write_fragments(args.out, rendered)
    total_ops = len(spec_operations(spec))
    print(
        f"wrote {len(rendered)} files to {args.out} "
        f"({len(spec['paths'])} paths / {total_ops} operations); "
        f"{len(changed)} changed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
