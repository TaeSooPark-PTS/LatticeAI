#!/usr/bin/env python3
"""Rebuild the OpenAPI contract from committed fragments and prove it exact.

README — what this is, and the one step that flips it on
========================================================

``frontend/openapi.json`` is the single committed contract and stays that way.
v11.6.0 moves most of the surface it describes out of Python, so the spec of
each migrated family lives beside the Rust crates as a committed fragment
(``rust/fixtures/openapi/<family>.json``, written by
``scripts/gen_openapi_fragments.py``). This script runs that split backwards:

    envelope + every fragment  ==  frontend/openapi.json,  byte for byte

Byte-for-byte is the whole point. It is compared as *bytes*, not as parsed
JSON, so a re-ordered key, a changed indent or an ``int`` that became a
``float`` all fail here instead of surfacing as client drift later.

**Cutover (Wave 3 / WP-P1) — one step, deliberately not taken yet.**
Today ``scripts/export_openapi.py`` builds the whole FastAPI app and dumps its
schema; this script only *verifies*. Once the Python app is reduced to the
worker surface, its schema no longer describes the product, and the pipeline
flips:

1. Keep ``scripts/export_openapi.py`` exporting the (now worker-only) app, but
   to a scratch path — it becomes an input, not the contract.
2. Regenerate ``rust/fixtures/openapi/worker_keep.json`` from that worker spec
   (``gen_openapi_fragments.py`` still owns the fragment shape); the other
   fragments are already committed and stop depending on Python entirely.
   ``info.version`` lives in ``_envelope.json``, so the fragments are
   regenerated **after** the version bump, never before — the composer refuses
   a worker spec whose version disagrees with theirs.
3. Point ``npm run frontend:openapi`` at
   ``scripts/compose_openapi.py --worker-spec <scratch> --output frontend/openapi.json``
   followed by the existing ``openapi-typescript`` step, so the committed
   contract is the composition rather than a Python dump.
4. Leave ``scripts/check_openapi_drift.mjs`` exactly as it is: it re-runs the
   generator into a temp dir and byte-compares. Composition is deterministic,
   so the gate keeps biting — it now bites on fragment drift too.

Until step 3 lands, ``--worker-spec`` is verification only: it asserts the
worker app's operations are a **subset** of the ``worker_keep`` fragment, i.e.
that Python has not grown a route the gateway would have to proxy without a
contract. That is the cutover's precondition, checkable today. The exception is
``EXEMPT_WORKER_PATHS``: WP-I6's internal ``/worker/*`` seams are loopback-only
and stay out of the public contract by design, and FastAPI's own docs
endpoints describe the schema rather than being surface. Both are named
explicitly below so nothing else slips through with them.

Usage::

    .venv/bin/python scripts/compose_openapi.py
    .venv/bin/python scripts/compose_openapi.py --worker-spec /tmp/worker.json
    .venv/bin/python scripts/compose_openapi.py --output /tmp/composed.json
"""

from __future__ import annotations

import argparse
import copy
import difflib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

# ``scripts`` is not a package, so the generator is loaded by path — the same
# trick the parity generators and their tests use. Importing it rather than
# retyping its constants is what keeps *one* serialiser in the pipeline: if the
# two halves could disagree about ``sort_keys`` or ``indent``, byte-exactness
# would be a coincidence instead of a property.
def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging accident
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fragments_module = _load_module(
    "gen_openapi_fragments",
    Path(__file__).resolve().parent / "gen_openapi_fragments.py",
)

SPEC_PATH = fragments_module.SPEC_PATH
FRAGMENT_DIR = fragments_module.FRAGMENT_DIR
ENVELOPE_NAME = fragments_module.ENVELOPE_NAME
INDEX_NAME = fragments_module.INDEX_NAME
WORKER_FAMILY = fragments_module.WORKER_FAMILY
canonical_json = fragments_module.canonical_json
sha256_text = fragments_module.sha256_text
load_json = fragments_module.load_json
operation_key = fragments_module.operation_key
spec_operations = fragments_module.spec_operations


#: WP-I6's internal worker seams. The gateway calls them over loopback behind
#: the seam gate; no client ever does, so they are deliberately absent from the
#: committed public contract and must not be filed into the ``worker_keep``
#: fragment. They are exempt from the subset check — and only they are.
INTERNAL_WORKER_PATHS = frozenset(
    {
        # ``/worker/chat/record-turn`` was the third state seam. WP-W3a moved
        # the history chain into lattice-chat and WP-P1 deleted the handler.
        "/worker/sysinfo",
        "/worker/llm/stream",
        # Wave 2.5 §W2 — the pure-compute seams. Same reasoning: the gateway
        # calls them over loopback behind the seam gate, and the shapes they
        # exchange (vectors, document bytes, parsed text) are an internal
        # protocol between lattice-host and its own worker, not client surface.
        "/worker/embed",
        "/worker/parse",
        "/worker/render/docx",
        "/worker/render/xlsx",
        "/worker/render/pptx",
        "/worker/render/pdf",
        "/worker/asr",
        # ``/worker/multimodal/describe`` was here until v11.8.0 deleted the
        # seam: it described an image for a native ingest nothing ever built.
        "/worker/extract",
        # v12.0.0 — HNSW sidecar query. Same loopback-only reasoning.
        "/worker/vector/query",
    }
)

#: FastAPI's own documentation endpoints. They are normally ``include_in_schema
#: = False`` and never reach ``paths`` (the committed contract has none of
#: them), but a worker app that turns docs on should not fail this check either
#: — they describe the schema, they are not product surface.
DOC_PATHS = frozenset({"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"})

#: Everything a worker spec may serve without a committed contract entry.
EXEMPT_WORKER_PATHS = INTERNAL_WORKER_PATHS | DOC_PATHS


class CompositionError(Exception):
    """A fragment set that cannot be reassembled into one coherent spec."""


def read_fragment_set(
    directory: Path,
) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]], List[str]]:
    """Load the envelope and every fragment ``_index.json`` lists.

    Returns ``(envelope, fragments_by_family, problems)``. Recorded sha256 values
    are checked here: a hand-edited fragment is reported as stale rather than
    silently composed, because the index is what the Rust tests and CI read.
    """
    index_path = directory / INDEX_NAME
    if not index_path.is_file():
        raise CompositionError(
            f"{index_path} is missing — run scripts/gen_openapi_fragments.py"
        )
    index = load_json(index_path)
    problems: List[str] = []

    envelope_path = directory / index["envelope"]["file"]
    if not envelope_path.is_file():
        raise CompositionError(f"{envelope_path} is missing — regenerate the fragments")
    envelope_text = envelope_path.read_text(encoding="utf-8")
    if sha256_text(envelope_text) != index["envelope"]["sha256"]:
        problems.append(f"{envelope_path.name}: sha256 does not match {INDEX_NAME}")
    envelope = load_json(envelope_path)

    fragments: Dict[str, Dict[str, Any]] = {}
    for entry in index["fragments"]:
        path = directory / entry["file"]
        if not path.is_file():
            raise CompositionError(f"{path} is missing — regenerate the fragments")
        text = path.read_text(encoding="utf-8")
        if sha256_text(text) != entry["sha256"]:
            problems.append(f"{entry['file']}: sha256 does not match {INDEX_NAME}")
        fragments[entry["family"]] = load_json(path)
    return envelope, fragments, problems


def merge_fragments(
    envelope: Mapping[str, Any],
    fragments: Mapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Reassemble one spec. Raises on a collision rather than picking a winner."""
    spec: Dict[str, Any] = copy.deepcopy(dict(envelope["spec"]))
    paths: Dict[str, Any] = {}
    owner: Dict[str, str] = {}
    schemas: Dict[str, Any] = dict(spec["components"]["schemas"])

    for family in sorted(fragments):
        fragment = fragments[family]
        for path, item in fragment["paths"].items():
            for method, operation in item.items():
                key = operation_key(method, path)
                if key in owner:
                    raise CompositionError(
                        f"{key} is claimed by both {owner[key]} and {family} — "
                        "one operation, one family"
                    )
                owner[key] = family
                paths.setdefault(path, {})[method] = operation
        for name, schema in fragment["components"]["schemas"].items():
            existing = schemas.get(name)
            if existing is not None and canonical_json(existing) != canonical_json(schema):
                raise CompositionError(
                    f"schema {name} differs between fragments (seen again in {family}) — "
                    "shared schemas must be byte-identical copies"
                )
            schemas[name] = schema

    spec["paths"] = paths
    spec["components"]["schemas"] = schemas
    return spec


def diff_report(composed_text: str, committed_text: str, limit: int = 40) -> List[str]:
    """A readable account of how the composition misses the committed bytes."""
    composed = load_json_text(composed_text)
    committed = load_json_text(committed_text)
    lines: List[str] = []

    composed_ops = {key for key, _, _ in spec_operations(composed)}
    committed_ops = {key for key, _, _ in spec_operations(committed)}
    for label, missing in (
        ("missing from the composition", sorted(committed_ops - composed_ops)),
        ("present only in the composition", sorted(composed_ops - committed_ops)),
    ):
        if missing:
            lines.append(f"{len(missing)} operation(s) {label}:")
            lines.extend(f"    {key}" for key in missing[:limit])
            if len(missing) > limit:
                lines.append(f"    … {len(missing) - limit} more")

    composed_schemas = set(composed["components"]["schemas"])
    committed_schemas = set(committed["components"]["schemas"])
    for label, names in (
        ("missing from the composition", sorted(committed_schemas - composed_schemas)),
        ("present only in the composition", sorted(composed_schemas - committed_schemas)),
    ):
        if names:
            lines.append(f"{len(names)} schema(s) {label}: {', '.join(names[:limit])}")

    delta = list(
        difflib.unified_diff(
            committed_text.splitlines(),
            composed_text.splitlines(),
            fromfile="frontend/openapi.json (committed)",
            tofile="composed from rust/fixtures/openapi/",
            lineterm="",
            n=1,
        )
    )
    if delta:
        lines.append(f"first {min(limit, len(delta))} diff line(s) of {len(delta)}:")
        lines.extend(f"    {line}" for line in delta[:limit])
    return lines


def load_json_text(text: str) -> Dict[str, Any]:
    parsed: Dict[str, Any] = json.loads(text)
    return parsed


def worker_spec_problems(
    worker_spec: Mapping[str, Any],
    worker_fragment: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> List[str]:
    """Check the worker app against the ``worker_keep`` fragment.

    Two things are asserted. First the subset: every operation the worker app
    still serves must have a committed contract in the fragment, or the gateway
    would be proxying a route nothing describes. Second the version: the
    fragments embed ``info.version``, so a bump that regenerated the worker spec
    without regenerating the fragments is drift — the same trap that reddens the
    drift gate when ``npm run frontend:openapi`` runs before the bump.

    ``EXEMPT_WORKER_PATHS`` is the one hole, and it is a named list rather than
    a pattern: WP-I6's internal seams are loopback-only and seam-gated, so they
    are product surface for the gateway alone and never enter the committed
    public contract. Anything else the worker grows still fails here.
    """
    problems: List[str] = []
    kept = {
        operation_key(method, path)
        for path, item in worker_fragment["paths"].items()
        for method in item
    }
    served = {
        key for key, path, _ in spec_operations(worker_spec) if path not in EXEMPT_WORKER_PATHS
    }
    extra = sorted(served - kept)
    if extra:
        problems.append(
            f"{len(extra)} worker operation(s) are not in the {WORKER_FAMILY} fragment — "
            "the gateway would have to proxy a route with no committed contract:"
        )
        problems.extend(f"    {key}" for key in extra)

    worker_version = worker_spec.get("info", {}).get("version")
    fragment_version = envelope["spec"]["info"]["version"]
    if worker_version != fragment_version:
        problems.append(
            f"worker spec is version {worker_version!r} but the fragments carry "
            f"{fragment_version!r} — regenerate the fragments after the bump"
        )
    return problems


def compose(
    directory: Path = FRAGMENT_DIR,
    spec_path: Path = SPEC_PATH,
    worker_spec_path: Path | None = None,
) -> Tuple[str, List[str]]:
    """Compose, verify, and return ``(composed_text, problems)``."""
    envelope, fragments, problems = read_fragment_set(directory)
    composed_text = canonical_json(merge_fragments(envelope, fragments))
    committed_text = spec_path.read_text(encoding="utf-8")

    if composed_text != committed_text:
        problems.append(
            f"composition does not reproduce {spec_path.name} byte for byte "
            f"({len(composed_text)} vs {len(committed_text)} bytes)"
        )
        problems.extend(diff_report(composed_text, committed_text))

    if worker_spec_path is not None:
        worker_spec = load_json(worker_spec_path)
        problems.extend(worker_spec_problems(worker_spec, fragments[WORKER_FAMILY], envelope))
    return composed_text, problems


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compose the OpenAPI contract from fragments.")
    parser.add_argument("--fragments", type=Path, default=FRAGMENT_DIR, help="fragment directory")
    parser.add_argument("--spec", type=Path, default=SPEC_PATH, help="committed contract")
    parser.add_argument(
        "--worker-spec",
        type=Path,
        default=None,
        help="OpenAPI schema of the Python worker app; asserted ⊆ the worker_keep fragment",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write the composed spec here (the P1 cutover writes frontend/openapi.json)",
    )
    args = parser.parse_args(argv)

    try:
        composed_text, problems = compose(args.fragments, args.spec, args.worker_spec)
    except CompositionError as error:
        print(f"openapi composition failed: {error}")
        return 1

    if problems:
        print("openapi composition failed:")
        for problem in problems:
            print(f"  {problem}")
        print("  fix: rerun scripts/gen_openapi_fragments.py and commit the fragments")
        return 1

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(composed_text, encoding="utf-8")
        print(f"wrote {args.output}")

    spec = load_json_text(composed_text)
    checked = f" (+worker spec ⊆ {WORKER_FAMILY})" if args.worker_spec else ""
    print(
        f"composed {len(spec['paths'])} paths / {len(spec_operations(spec))} operations "
        f"from {args.fragments} — byte-identical to {args.spec}{checked}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
