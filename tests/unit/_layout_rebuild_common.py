"""Shared fixtures for the layout-rebuild API contract suites.

The layout-rebuild tests split across three files — run timelines, mock↔real
shape parity, and the orphan i18n gate — but they lean on the same doubles:
an in-memory graph, an in-memory workspace store, and the parsers that read
the visual mock and the frontend i18n tables as text.

This module holds nothing that pytest collects; it is imported by
``test_layout_rebuild_*.py`` so a fixture has exactly one definition.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.knowledge_graph import create_knowledge_graph_router


class _PipelineGraph:
    def __init__(
        self,
        documents: List[Dict[str, Any]],
        edges: Dict[str, int],
        *,
        index_status: Optional[Dict[str, Any]] = None,
    ):
        self._documents = documents
        self._edges = edges
        self._index_status = index_status

    def stats(self):
        return {
            "schema_version": 3,
            "v2_schema_available": True,
            "nodes": {"Document": len(self._documents), "Chunk": 4},
            "edges": self._edges,
            "v2": {"nodes": len(self._documents), "edges": sum(self._edges.values())},
        }

    def list_documents(self, limit: int = 200):
        docs = self._documents[:limit]
        return {"documents": docs, "total": len(docs)}

    def index_status(self):
        if self._index_status is None:
            raise AttributeError("index_status not configured")
        return self._index_status

    def graph(self, limit):
        return {"nodes": [], "edges": [], "limit": limit}

    def search(self, q, limit):
        return {"query": q, "matches": []}

    def context_for_query(self, q, limit):
        return ""

    def neighbors(self, node_id):
        return {"node": node_id, "neighbors": []}

    def ingest_message(self, role, content, **kwargs):
        return {"status": "ok"}

    def curate(self):
        return {"status": "ok"}

    def provenance_coverage(self):
        return {"total_nodes": 0, "nodes_with_provenance": 0, "coverage_ratio": 0}


class _MemoryStore:
    def __init__(self, state: Dict[str, Any]):
        self._state = state

    def load_state(self):
        return self._state

    def save_state(self, state):
        self._state = state

    def _scoped(self, items, workspace_id):
        if not workspace_id:
            return list(items)
        return [
            item
            for item in items
            if str(item.get("workspace_id") or "personal") == str(workspace_id)
        ]

    def _resolve_scope(self, workspace_id, state):
        return workspace_id or "personal"

    def _record_workspace(self, run):
        return str(run.get("workspace_id") or "personal")

    def _emit_execution_event(self, **kwargs):
        return None

    def record_timeline_event(self, *args, **kwargs):
        return None


def _kg_client(graph) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_knowledge_graph_router(
            get_graph=lambda: graph,
            require_graph=lambda: None,
            require_user=lambda _request: "user@example.com",
            static_dir=Path("."),
        )
    )
    return TestClient(app)


def _assert_stage_invariants(stage: Dict[str, Any]) -> None:
    """pending=0 must never pair with waiting when count>0, or with working."""
    assert stage["pending"] >= 0
    assert stage["count"] >= 0
    if stage["pending"] == 0:
        assert stage["status"] != "working"
        if stage["count"] > 0:
            assert stage["status"] != "waiting"
            assert stage["status"] == "done"
    if stage["pending"] > 0:
        assert stage["status"] == "working"


def _mixed_workspace_state() -> Dict[str, Any]:
    """Agent runs in workspace A + workflow runs in workspace B (and one A)."""
    return {
        "agent_runs": [
            {
                "id": "agent-a-1",
                "status": "ok",
                "goal": "Workspace A agent",
                "created_at": "2026-06-06T12:30:00",
                "workspace_id": "ws-a",
            },
            {
                "id": "agent-b-1",
                "status": "running",
                "goal": "Workspace B agent",
                "created_at": "2026-06-06T12:40:00",
                "workspace_id": "ws-b",
            },
        ],
        "workflow_runs": [
            {
                "id": "wf-a-1",
                "workflow_id": "wf-a",
                "workflow_name": "Workspace A workflow",
                "status": "awaiting_approval",
                "created_at": "2026-06-06T12:05:00",
                "workspace_id": "ws-a",
            },
            {
                "id": "wf-b-1",
                "workflow_id": "wf-b",
                "workflow_name": "Workspace B workflow",
                "status": "ok",
                "created_at": "2026-06-06T12:00:00",
                "workspace_id": "ws-b",
            },
        ],
    }


def _parse_mock_sysinfo() -> Dict[str, Any]:
    """Extract the /local/sysinfo payload fields from the visual mock server.

    Release capture (`08-system.png`) hits this mock in basic mode. The mock
    must ship the same readiness bucket the real API would compute for its
    percents — otherwise the published screenshot can say "넉넉합니다" while
    ram_pct is 61 (tight).

    Keys in the mock are unquoted JS identifiers, so we parse fields with
    regex rather than ``json.loads``.
    """
    import re

    source = _mock_server_source()
    match = re.search(
        r'pathname === "/local/sysinfo"[^{]*return json\(res,\s*(\{.*?)\);',
        source,
        re.DOTALL,
    )
    assert match, "mock_server.cjs must define /local/sysinfo"
    body = match.group(1)

    def _num(name: str) -> float:
        m = re.search(rf"\b{name}\s*:\s*([0-9]+(?:\.[0-9]+)?)", body)
        assert m, f"mock /local/sysinfo missing numeric field {name}"
        return float(m.group(1))

    def _str(name: str) -> str:
        m = re.search(rf'\b{name}\s*:\s*"([^"]+)"', body)
        assert m, f"mock /local/sysinfo missing string field {name}"
        return m.group(1)

    return {
        "cpu_pct": _num("cpu_pct"),
        "ram_pct": _num("ram_pct"),
        "gpu_mem_pct": _num("gpu_mem_pct"),
        "gpu_mem_gb": _num("gpu_mem_gb"),
        "readiness": _str("readiness"),
    }


def _readiness_copy_from_workspace_i18n() -> Dict[str, str]:
    """Parse ko readiness phrases from frontend/src/i18n/workspace.ts.

    Hand-copied constants drift silently when i18n is edited. Reading the
    source of truth keeps mock bucket ↔ UI copy agreement honest.
    """
    import re

    i18n_dir = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"
    # The workspace namespace is an aggregator plus per-domain part files
    # (workspace.ts + workspace/*.ts); each part carries its own ko/en blocks.
    # Concatenate every ko block so the keys are found wherever they live.
    ko_bodies: List[str] = []
    for i18n_path in [i18n_dir / "workspace.ts", *sorted((i18n_dir / "workspace").glob("*.ts"))]:
        source = i18n_path.read_text(encoding="utf-8")
        ko_match = re.search(r"\bko\s*:\s*\{", source)
        if not ko_match:
            continue
        ko_start = ko_match.end()
        en_match = re.search(r"\ben\s*:\s*\{", source[ko_start:])
        ko_bodies.append(
            source[ko_start : ko_start + en_match.start()] if en_match else source[ko_start:]
        )
    ko_body = "\n".join(ko_bodies)
    assert ko_body, "workspace i18n must define a ko copy block"

    out: Dict[str, str] = {}
    for bucket in ("roomy", "tight", "low"):
        m = re.search(
            rf'"system\.readiness\.{bucket}"\s*:\s*"([^"]+)"',
            ko_body,
        )
        assert m, f"workspace i18n ko missing system.readiness.{bucket}"
        out[bucket] = m.group(1)
    return out


# ── mock_server.cjs ↔ real API shape parity (layout-rebuild capture surfaces) ──


def _mock_server_source() -> str:
    """Concatenated source of the visual mock: entry plus its route modules.

    ``mock_server.cjs`` is a thin entry that composes the route modules under
    ``tests/visual/mock_server/``; a route branch and the inline payload it
    returns always live together in whichever module owns it. Reading the whole
    tree as one blob keeps the parsers below finding a branch wherever it moved.
    """
    visual = Path(__file__).resolve().parents[1] / "visual"
    entry = visual / "mock_server.cjs"
    assert entry.is_file(), f"missing visual mock: {entry}"
    parts = [entry.read_text(encoding="utf-8")]
    parts.extend(
        path.read_text(encoding="utf-8")
        for path in sorted((visual / "mock_server").glob("*.cjs"))
    )
    return "\n".join(parts)


def _extract_mock_json_object(source: str, pathname: str) -> Dict[str, Any]:
    """Best-effort extract of ``return json(res, {…})`` for a mock pathname.

    The visual mock is plain JS, not JSON. We locate the pathname branch and
    hand the object body to ``json.loads`` after a small identifier→string
    rewrite (unquoted keys, trailing commas, bare null/true/false stay valid
    enough for the three layout-rebuild endpoints we pin).
    """
    import json
    import re

    # Match both single-line and multi-line ``return json(res, {…})`` forms.
    # Non-greedy body up to the matching close is hard with nested braces, so
    # we brace-count from the first ``{`` after the pathname hit.
    path_idx = source.find(f'pathname === "{pathname}"')
    if path_idx < 0:
        path_idx = source.find(f"pathname === '{pathname}'")
    assert path_idx >= 0, f"mock_server.cjs missing branch for {pathname}"
    window = source[path_idx : path_idx + 4000]
    ret_idx = window.find("return json(res,")
    assert ret_idx >= 0, f"mock branch for {pathname} has no return json(res, …)"
    brace_start = window.find("{", ret_idx)
    assert brace_start >= 0, f"mock branch for {pathname} has no object body"
    depth = 0
    end = None
    for i, ch in enumerate(window[brace_start:], start=brace_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, f"unbalanced braces in mock branch for {pathname}"
    body = window[brace_start:end]
    # Quote bare identifiers used as object keys: ``action:`` → ``"action":``
    # but leave already-quoted keys and string values alone.
    quoted = re.sub(r"(?m)([{\[,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', body)
    # Drop trailing commas before } or ] (JS allows them; JSON does not).
    quoted = re.sub(r",\s*([}\]])", r"\1", quoted)
    try:
        return json.loads(quoted)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"failed to parse mock JSON for {pathname}: {exc}\n{quoted[:500]}"
        ) from exc


def _deep_key_set(value: Any, *, prefix: str = "") -> set[str]:
    """Collect dotted key paths for dict/list JSON-ish payloads."""
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            keys.add(path)
            keys |= _deep_key_set(v, prefix=path)
    elif isinstance(value, list) and value:
        # Sample first element only — mock arrays are homogeneous for these APIs.
        keys |= _deep_key_set(value[0], prefix=f"{prefix}[]" if prefix else "[]")
    return keys


def _workspace_i18n_keys() -> set[str]:
    """Parse quoted keys from frontend/src/i18n/workspace.ts (ko + en blocks)."""
    import re

    i18n_dir = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n"
    # Aggregator + per-domain parts (workspace.ts + workspace/*.ts).
    keys: set[str] = set()
    for i18n_path in [i18n_dir / "workspace.ts", *sorted((i18n_dir / "workspace").glob("*.ts"))]:
        source = i18n_path.read_text(encoding="utf-8")
        keys.update(re.findall(r'"((?:act\.approval\.action\.)[^"]+)"\s*:', source))
    return keys


# ── orphan i18n key gate ────────────────────────────────────────────────────
#
# Runtime-assembled keys use ``t(`prefix.${...}`)``. Those prefixes are
# allowlisted so the gate does not false-positive on every dynamic key.
# Anything still unreferenced after the allowlist is an orphan.
#
# Baseline: tests/unit/fixtures/i18n_known_orphans.txt
#   - Section 1 = true legacy (orphans already present at git tag v10.6.3)
#   - Section 2 = layout-rebuild 2026-08 residual (NOT forever-frozen;
#     frontend should delete unused i18n entries, then remove them here).
#     Cap = 157 (legacy) + current Section 2 residual. Do NOT raise the cap
#     to bless new orphans — shrink Section 2 and lower the cap instead.

# Hard ceiling = 157 legacy + residual Section 2. Recounted after frontend
# round: Section 2 still has 11 keys (frontend did not delete them), so the
# honest ceiling is 168. Raising above that is a reject; lowering is required
# whenever Section 2 shrinks.
I18N_ORPHAN_FIXTURE_CAP = 157  # legacy only; the 11 section-2 keys were deleted, not tolerated

# Explicit allowlist for ``t(`prefix.${...}`)`` / concat assembly. Keep this
# list in the test file so reviews can see every runtime-prefix exception.
I18N_DYNAMIC_PREFIX_ALLOWLIST = (
    "act.approval.action.",
    "act.cadence.",
    "act.creates.",
    "act.recipe.",
    "act.runStatus.",
    "act.trigger.when.",
    "act.agentRole.",
    "brain.answerProof.confidence.",
    "brain.firstScreen.state.",
    "brain.garden.bed.",
    "brain.garden.empty.",
    "brain.headline.",
    "brain.ingest.",
    "brain.jobs.status.",
    "brain.living.state.",
    "brain.memoryTier.",
    "brain.proactive.status.",
    "brain.readiness.",
    "brain.depth.",
    "brain.depthTitle.",
    "brain.rings.",
    "capture.pipeline.step.",
    "flow.install.stage.",
    "flow.install.step.",
    "intelligence.action.",
    "intelligence.dim.",
    "intelligence.grade.",
    "library.model.status.",
    "shell.mode.",
    "shell.sync.",
    "system.permission.mode.",
    "system.permission.risk.",
    "system.readiness.",
    "ui.entity.",
    "ui.field.",
)


def _discover_defined_i18n_keys(repo: Path) -> set[str]:
    import re

    keys: set[str] = set()
    i18n_dir = repo / "frontend" / "src" / "i18n"
    # Namespaces are aggregators with per-domain part files in subdirectories
    # (i18n/<ns>.ts + i18n/<ns>/*.ts) — rglob covers both layouts.
    for path in sorted(i18n_dir.rglob("*.ts")):
        if path.stem in {"types", "registry"}:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'^\s+"([^"]+)":\s*"', text, re.M):
            keys.add(match.group(1))
    return keys


def _frontend_src_blob_excluding_i18n_defs(repo: Path) -> str:
    parts: List[str] = []
    src = repo / "frontend" / "src"
    for path in src.rglob("*"):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        # Skip namespace definition tables — keys only appear there as defs.
        # Covers both the aggregators (i18n/<ns>.ts) and their per-domain part
        # files (i18n/<ns>/*.ts).
        if (src / "i18n") in path.parents and path.stem not in {"types", "registry"}:
            continue
        # Tests are not a use site. A spec asserting `t(lang, "x.y")` renders
        # the right copy does not put that copy on any screen, so counting the
        # test blob let a key look wired up because it was *tested*. That is
        # the exact dishonesty this gate exists to catch, and it went live the
        # moment 10.10.0 gave the frontend a full test suite: thirteen keys no
        # panel renders started reading as "re-wired".
        if ".test." in path.name or path.parent.name == "test":
            continue
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _discover_orphan_i18n_keys(repo: Path) -> set[str]:
    defined = _discover_defined_i18n_keys(repo)
    blob = _frontend_src_blob_excluding_i18n_defs(repo)
    orphans: set[str] = set()
    for key in defined:
        if any(key.startswith(prefix) for prefix in I18N_DYNAMIC_PREFIX_ALLOWLIST):
            continue
        if f'"{key}"' in blob or f"'{key}'" in blob or f"`{key}`" in blob:
            continue
        orphans.add(key)
    return orphans


def _load_known_orphan_baseline(repo: Path) -> set[str]:
    path = repo / "tests" / "unit" / "fixtures" / "i18n_known_orphans.txt"
    assert path.is_file(), f"missing orphan baseline fixture: {path}"
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        keys.add(line)
    return keys
