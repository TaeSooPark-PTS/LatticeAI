#!/usr/bin/env python3
"""Capture golden HTTP fixtures for the brain / chat / knowledge route families.

v11.6.0 ("One Door") moves the platform route box from Python to the Rust
gateway and then **deletes** the Python routers. A port is only a port if
something proves it answers the same way, and the only witness that can prove
it is the implementation that is about to be removed. So, before the delete:
drive the *real* FastAPI app (``latticeai.app_factory.create_app``) over a
seeded temporary data directory with ``TestClient`` and write every
request/response pair to ``rust/fixtures/http/``. The Rust tests replay them.

Scope — WP-F2 (this file owns exactly these families)::

    chat.json            chat, chat_stream, chat_history, chat_documents,
                         chat_intents, chat_contracts, chat_helpers,
                         chat_agent_http, chat_hybrid  (+ the GET /chat redirect,
                         captured here for the GET/POST collision contract even
                         though static_routes.py owns the handler)
    memory_brain.json    memory, brain_intelligence, garden, chronicle,
                         command_center, evidence_actions
    knowledge_search.json search, knowledge_graph, index_jobs, local_files,
                         local_knowledge, browser

Record shape (one JSON object per captured exchange, in ``cases``)::

    {"family", "method", "path", "query", "request_headers", "request_body",
     "status", "response_headers", "response_body",
     "name", "note", "sse_frames"}

``name`` (a stable case id) and ``note`` (why the case exists) are additive and
carry no contract; ``sse_frames`` replaces ``response_body`` for
``text/event-stream`` answers and holds the parsed wire frames in order:
``[{"event": null|"agent_step", "data": "…"}, …]`` including the terminating
``data: [DONE]`` sentinel. Frame payloads that parse as JSON are re-serialised
with ``json.dumps(…, ensure_ascii=False)`` — byte-identical to how the server
wrote them (``chat_stream.py`` uses exactly those flags) — after the same
normalisation the JSON bodies get.

Matcher tokens (a replayer compares against these, it does not expect a
literal): ``@ts`` a timestamp, ``@uuid`` a UUID, ``@any`` a value that is
allowed to differ per machine/run (paths outside the sandbox, random tokens,
IPs, durations, pids). Sandbox paths are rewritten to ``@datadir/…``,
``@home/…``, ``@corpus/…``, ``@agentroot/…``, ``@repo/…`` so the *shape* of a
path answer stays checkable, and so are ``@today`` (the capture date),
``@hostname``, ``@systmp``, ``@tmpname``, ``@stamp`` and the ``@garden*`` ids.
Every token is listed, with its meaning, in each file's ``tokens`` header.
``@session`` in ``request_headers.cookie`` marks "this call carried the
logged-in session cookie" (no token is committed).

Determinism (the hard part; each of these was found by diffing two runs):

* a **fixed** sandbox path, because the local-knowledge index hashes the
  absolute folder path into its source/local-file/chunk ids;
* ``PYTHONHASHSEED=0`` (this script re-execs itself to set it), because the
  curator ranks candidates out of a set of strings;
* ``frozen_stamps`` — the write clock the graph stamps rows with is frozen for
  the whole capture, because rows are read back ``ORDER BY created_at ASC,
  id ASC`` over a **second-resolution** column: one request that writes five
  nodes while a second boundary passes reorders them, and no amount of pacing
  makes that not a race (it is what made two "identical" runs disagree on the
  third);
* :func:`settle` between capture phases anyway, for any row stamped by a path
  the freeze does not reach;
* a pinned spool name for the seeding upload, whose random temp path would
  otherwise become part of a Source node's id;
* hash embeddings (no ML), rules-only extraction, ``LATTICE_TZ=UTC``, ids that
  are content hashes, explicit date/ts parameters for the bitemporal routes;
* everything left over — clock readings, decayed scores, random tokens, machine
  facts, and one list whose *order* (not content) nothing determines —
  replaced by tokens.

Running this script twice produces byte-identical files. That two-run diff is
the determinism proof, and it is how every rule above was found.

Honest gaps are listed in ``gaps`` in each file and re-printed at the end.

Usage::

    .venv/bin/python scripts/gen_http_fixtures_brain.py
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import socket
import sqlite3
import sys
import tempfile
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
# The repo (for the product packages) and this directory: `scripts` is not a
# package, so the harness and the three case tables are imported by name off
# the script directory — the convention the parity generator already uses for
# its corpus modules.
for _import_root in (REPO_ROOT, Path(__file__).resolve().parent):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

import http_fixture_brain_chat as chat_cases  # noqa: E402
import http_fixture_brain_knowledge as knowledge_cases  # noqa: E402
import http_fixture_brain_memory as memory_cases  # noqa: E402
from http_fixture_brain_harness import (  # noqa: E402
    BURST_EMAIL,
    BURST_PASSWORD,
    CORPUS_FILES,
    DROPPED_HEADERS,
    MEMBER_EMAIL,
    MEMBER_PASSWORD,
    OWNER_EMAIL,
    OWNER_PASSWORD,
    PINNED_ENV,
    Normalizer,
    Recorder,
    approved_token,
    frozen_stamps,
    seed,
    settle,
)

OUT_DIR = REPO_ROOT / "rust" / "fixtures" / "http"
BRAIN_STORE = OUT_DIR / "brain_store.sqlite"
BRAIN_STORE_DUMP = OUT_DIR / "brain_store.dump.json"

# Logical tables the capture seed writes. Frozen so a second run can prove
# dump-identity even when the sqlite file's pager bytes are not byte-identical
# (WAL leftovers, freelist). Blobs are hex; rows ordered by a superkey.
_BRAIN_STORE_TABLES = (
    "graph_meta",
    "kg_meta",
    "nodes",
    "edges",
    "chunks",
    "nodes_v2",
    "edges_v2",
    "edge_occurrences",
    "knowledge_sources",
    "local_file_index",
    "vector_embeddings",
    "vector_jobs",
    "vector_index_operations",
    "ingestion_provenance",
    "conversation_messages",
    "workspace_os_state",
)
_BRAIN_STORE_ORDER = {
    "graph_meta": "key",
    "kg_meta": "key",
    "nodes": "id",
    "edges": "id",
    "chunks": "id",
    "nodes_v2": "id",
    "edges_v2": "id",
    "edge_occurrences": "id",
    "knowledge_sources": "id",
    "local_file_index": "id",
    "vector_embeddings": "item_id",
    "vector_jobs": "id",
    "vector_index_operations": "id",
    "ingestion_provenance": "id",
    "conversation_messages": "id",
    "workspace_os_state": "id",
}

#: family → output file. Every recorded case names a family; the family decides
#: which of the three files it lands in.
FAMILY_FILE: Dict[str, str] = {
    "chat": "chat",
    "chat_ui": "chat",
    "chat_history": "chat",
    "chat_agent_http": "chat",
    "memory": "memory_brain",
    "brain_intelligence": "memory_brain",
    "garden": "memory_brain",
    "chronicle": "memory_brain",
    "command_center": "memory_brain",
    "evidence_actions": "memory_brain",
    "search": "knowledge_search",
    "knowledge_graph": "knowledge_search",
    "index_jobs": "knowledge_search",
    "local_files": "knowledge_search",
    "local_knowledge": "knowledge_search",
    "browser": "knowledge_search",
}

FILE_SCOPE: Dict[str, str] = {
    "chat": (
        "latticeai/api/chat.py, chat_stream.py, chat_history.py, "
        "chat_documents.py, chat_intents.py, chat_contracts.py, "
        "chat_helpers.py, chat_agent_http.py, chat_hybrid.py "
        "(+ GET /chat, whose handler lives in static_routes.py)"
    ),
    "memory_brain": (
        "latticeai/api/memory.py, brain_intelligence.py, garden.py, "
        "chronicle.py, command_center.py, evidence_actions.py"
    ),
    "knowledge_search": (
        "latticeai/api/search.py, knowledge_graph.py, index_jobs.py, "
        "local_files.py, browser.py + latticeai/services/local_knowledge.py "
        "(mounted through local_files.py)"
    ),
}

GAPS: List[str] = [
    "POST /chat with a model loaded is NOT captured: loading an MLX model is "
    "the AI worker's job and would make the fixture machine-dependent. What is "
    "captured is every branch reachable without one — the no-model refusal "
    "(all four stream/Accept combinations), the intent branches that answer "
    "before the model is consulted (/clear, current-url, direct file action), "
    "the unknown-model 404, the identity-mismatch 403, the 422 and the 401.",
    "Therefore no /chat SSE success stream exists in these fixtures. The only "
    "SSE capture is POST /agent (stream:true), whose no-model failure still "
    "exercises the real frame writer: an anonymous error frame followed by the "
    "'data: [DONE]' sentinel. Named 'agent_step' frames are unreachable "
    "without a model (the loop fails before its first observer callback) — the "
    "Rust port must pin those against rust/fixtures/agent_loop instead.",
    "POST /chat 'network status' intent: captured with response_body '@any'. "
    "It shells out for the machine's own interfaces and calls an external IP "
    "service, so its body is neither offline-reproducible nor machine-stable. "
    "Status code and content type are the whole contract here.",
    "POST /api/browser/read-url is captured on its validation/boundary "
    "branches only (bad scheme, private host, missing url) — the success "
    "branch fetches a public URL and this generator makes no network calls. "
    "POST /api/browser/ingest-current-tab, which needs no network, is "
    "captured on its real success branch.",
    "The local-file approval dance (/local/*, /api/ingestion/*, "
    "/knowledge-graph/local/*) needs a human approval that is granted through "
    "POST /permissions/approve/{token} — another WP's family. That call is "
    "made as a seeding step and is not recorded here; the fixtures show the "
    "probe response (which mints the token), the approved success and the "
    "unapproved/expired/absent-token refusals.",
    "Approval tokens are random per run, so they appear as '@any' in both the "
    "response that minted them and the request that spends them. A replayer "
    "must thread the value it received rather than the literal.",
    "Feature-gate refusals (gate_read/gate_write) are not captured: every gate "
    "these families read is on by default, and flipping toggles belongs to the "
    "feature-toggle WP. All /api/memory and /api/brain captures are therefore "
    "gate-open captures.",
    "Workspace scoping is captured single-workspace only (the seeded owner has "
    "no second workspace). Cross-workspace 403s belong to the workspace WP.",
    "POST /api/index/{drain,rebuild} are KEEP_WORKER: captured for the worker "
    "contract, not as a port target.",
    "Three cases answer with facts about the machine that captured them and "
    "are marked MACHINE-DEPENDENT in their note: local_knowledge/roots (this "
    "machine's home and mounted volumes), local_files/local_agent_status "
    "(platform, machine, python, pid — already tokenised) and "
    "local_files/ingestion_interop_status (whether git is installed). Replay "
    "their shape, not their values.",
    "The capture date appears as the '@today' token (in the chronicle day path "
    "and as-of query). A replayer substitutes its own date; nothing else in "
    "these files is dated, because every other timestamp is '@ts'.",
    "One list is '@any' because its ORDER is not determined by anything a "
    "caller can pin, even with the write clock frozen and PYTHONHASHSEED set: "
    "knowledge_graph/curate's 'skipped', whose equal-score candidates keep "
    "their clustering order. Its contents are stable and its member shape is "
    "in the case note; everything else in that answer is pinned.",
    "Found while capturing, recorded rather than fixed: GET /api/command/search "
    "can never fill its 'knowledge' group — services/command_center.py reads "
    "payload['results'] from search_service.keyword_search, which answers "
    "'matches'. The fixtures show the empty group, which is what the product "
    "does today.",
]

#: Every environment knob these families read, pinned to the profile the
#: fixtures describe: local mode, loopback bind, auth required (so the 401
#: branches are real), open registration (to seed the owner), graph on,
#: hash embeddings (deterministic, no ML), UTC.
# ── driver ───────────────────────────────────────────────────────────────────


def build_environment(root: Path) -> Dict[str, Path]:
    home = root / "home"
    data = root / "data"
    corpus = root / "corpus"
    agent_root = root / "agent_workspace"
    for path in (home, data, corpus, agent_root):
        path.mkdir(parents=True, exist_ok=True)
    frozen_mtime = 1_786_000_000  # 2026-08-14T12:26:40Z — after the frozen stamp day
    for name, text in CORPUS_FILES.items():
        path = corpus / name
        path.write_text(text, encoding="utf-8")
        os.utime(path, (frozen_mtime, frozen_mtime))

    os.environ.update(PINNED_ENV)
    os.environ.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LATTICEAI_DATA_DIR": str(data),
        "LATTICEAI_AGENT_ROOT": str(agent_root),
        "LATTICEAI_LOCAL_ROOTS": str(corpus),
    })
    return {"home": home, "data": data, "corpus": corpus, "agent_root": agent_root}


def login(app, email: str, password: str, *, register: bool = True):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    if register:
        client.post("/register", json={
            "email": email, "password": password,
            "name": email.split("@")[0], "nickname": email.split("@")[0],
        })
    response = client.post("/login", json={"email": email, "password": password})
    response.raise_for_status()
    return client


def _cell(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    return value


_ISO_WALL = __import__("re").compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)


def _rewrite(value: Any, replacements: List[tuple], frozen: str) -> Any:
    if isinstance(value, str):
        out = value
        for old, new in replacements:
            out = out.replace(old, new)
        out = _ISO_WALL.sub(
            lambda match: frozen if match.group(0).startswith(frozen[:10]) else match.group(0),
            out,
        )
        return out
    if isinstance(value, list):
        return [_rewrite(item, replacements, frozen) for item in value]
    if isinstance(value, dict):
        return {key: _rewrite(item, replacements, frozen) for key, item in value.items()}
    return value


def dump_brain_store(
    db_path: Path, replacements: List[tuple], frozen: str
) -> Dict[str, Any]:
    """Canonical table dump — dump-identical across two runs even if the
    sqlite pager bytes are not."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        present = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        dump: Dict[str, Any] = {"tables": {}}
        for table in _BRAIN_STORE_TABLES:
            if table not in present:
                dump["tables"][table] = None
                continue
            order = _BRAIN_STORE_ORDER[table]
            rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
            dump["tables"][table] = [
                {key: _cell(row[key]) for key in row.keys()} for row in rows
            ]
        dump["user_version"] = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
        dump = _rewrite(dump, replacements, frozen)
        payload = json.dumps(dump, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        dump["sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return dump
    finally:
        conn.close()


def persist_brain_store(
    data_dir: Path, replacements: List[tuple], frozen: str
) -> Path:
    """Freeze the Python-seeded ``knowledge_graph.sqlite`` next to the HTTP
    fixtures so the Rust replay harness can open the same rows the capture
    read.

    Checkpoint + ``VACUUM INTO`` is the closest we get to byte-stable sqlite;
    the canonical dump beside it is the identity proof if pager bytes drift.
    """
    src = data_dir / "knowledge_graph.sqlite"
    if not src.exists():
        raise SystemExit(f"seed produced no {src}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
        BRAIN_STORE.unlink(missing_ok=True)
        # VACUUM INTO copies a compacted, checkpointed database. Quote the
        # destination as a SQL string — the path is ours, not caller input.
        dest_sql = str(BRAIN_STORE).replace("'", "''")
        conn.execute(f"VACUUM INTO '{dest_sql}'")
    finally:
        conn.close()
    dump = dump_brain_store(BRAIN_STORE, replacements, frozen)
    BRAIN_STORE_DUMP.write_text(
        json.dumps(dump, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return BRAIN_STORE


def write_files(cases: List[Dict[str, Any]]) -> Dict[str, int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    buckets: Dict[str, List[Dict[str, Any]]] = {
        name: [] for name in sorted(set(FAMILY_FILE.values()))
    }
    for case in cases:
        buckets[FAMILY_FILE[case["family"]]].append(case)

    counts: Dict[str, int] = {}
    for name, bucket in buckets.items():
        families: Dict[str, int] = {}
        for case in bucket:
            families[case["family"]] = families.get(case["family"], 0) + 1
        document = {
            "_": (
                "Golden HTTP fixtures captured from the live Python app by "
                "scripts/gen_http_fixtures_brain.py before the Python routers "
                "are deleted (v11.6.0 One Door, WP-F2). Replayed by the Rust "
                "port. Do not hand-edit — regenerate."
            ),
            "scope": FILE_SCOPE[name],
            "families": families,
            "case_count": len(bucket),
            "order_is_significant": (
                "Cases were captured in this order against one shared sandbox; "
                "writes and the destructive cases at the end depend on it."
            ),
            "tokens": {
                "@ts": "a timestamp (any ISO-8601 value)",
                "@uuid": "a UUID",
                "@any": "a value that may differ per run or per machine "
                        "(random tokens, durations, pids, IPs, out-of-sandbox "
                        "paths, machine facts)",
                "@session": "in request_headers.cookie: the call carried the "
                            "logged-in session cookie",
                "@datadir": "the sandbox data directory",
                "@home": "the sandbox HOME",
                "@corpus": "the seeded local corpus directory",
                "@agentroot": "the agent workspace root",
                "@sandbox": "the sandbox root the four above live in",
                "@repo": "the repository checkout",
                "@systmp": "the system temp directory",
                "@tmpname": "a random temp-file name the product minted",
                "@stamp": "a YYYYMMDD_HHMMSS stamp in a garden filename",
                "@today": "the capture date (YYYY-MM-DD); a replayer "
                          "substitutes its own",
                "@hostname": "this machine's name, which the discovery index "
                             "writes into a Computer node and its id",
                "@gardenwiki0/@gardenwiki1/@gardenraw0/@gardenraw1": "the "
                    "graph_node_id and provenance_id of the two garden writes; "
                    "they hash a filename carrying the wall-clock second, so "
                    "they are tokenised wherever they appear",
            },
            "record_shape": [
                "family", "method", "path", "query", "request_headers",
                "request_body", "status", "response_headers", "response_body",
                "name", "note", "sse_frames",
            ],
            "sse": (
                "For text/event-stream answers response_body is null and "
                "sse_frames holds the parsed wire frames in order, including "
                "the terminating 'data: [DONE]' sentinel. JSON payloads are "
                "re-serialised with json.dumps(ensure_ascii=False), the flags "
                "the server itself writes them with."
            ),
            "dropped_response_headers": sorted(DROPPED_HEADERS),
            "gaps": GAPS,
            "cases": bucket,
        }
        path = OUT_DIR / f"{name}.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        counts[name] = len(bucket)
    return counts


def main() -> int:
    # Curation ranks its candidates out of a set of strings, so the promoted
    # order follows PYTHONHASHSEED. Pin it and start over: it can only be set
    # before the interpreter comes up.
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(  # noqa: S606 — re-running this very script, no shell involved
            sys.executable,
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
        )

    # A *fixed* sandbox path, not mkdtemp's random one: the local-knowledge
    # index derives source/local-file/chunk ids by hashing the absolute folder
    # path, so a random directory would move every one of those ids between
    # runs and nothing would ever be byte-identical twice.
    root = Path(tempfile.gettempdir()).resolve() / "lattice-http-fixtures-brain"
    shutil.rmtree(root, ignore_errors=True)
    # The frozen write clock is entered once the app exists and released in
    # the finally below, so a failure never leaves the patch installed.
    stack = ExitStack()
    try:
        paths = build_environment(root)
        # Both spellings of every sandbox path: macOS resolves /var to
        # /private/var, so the app answers with the resolved form while the
        # requests carry the unresolved one.
        replacements: List[tuple] = []
        for name, path in (
            ("@corpus", paths["corpus"]), ("@agentroot", paths["agent_root"]),
            ("@datadir", paths["data"]), ("@home", paths["home"]),
            ("@sandbox", root),
        ):
            for spelling in {str(path), str(path.resolve())}:
                replacements.append((spelling, name))
        replacements.append((str(REPO_ROOT), "@repo"))
        replacements.append((str(Path(tempfile.gettempdir()).resolve()), "@systmp"))
        # The discovery index writes a Computer node titled with this machine's
        # name, and its id carries the same string.
        for name in {socket.gethostname(), platform.node()}:
            if name:
                replacements.append((name, "@hostname"))
                replacements.append((name.lower(), "@hostname"))
        today = date.today().isoformat()
        replacements.append((today, "@today"))
        normalizer = Normalizer(replacements)

        from latticeai.app_factory import create_app

        # Freeze *before* create_app: schema bootstrap stamps
        # kg_meta.v2_write_mastered_at and workspace_os created_at.
        stack.enter_context(frozen_stamps(today))
        app = create_app()
        owner = login(app, OWNER_EMAIL, OWNER_PASSWORD)
        member = login(app, MEMBER_EMAIL, MEMBER_PASSWORD)
        burst = login(app, BURST_EMAIL, BURST_PASSWORD)
        from fastapi.testclient import TestClient

        anon = TestClient(app)

        facts = seed(owner, paths["corpus"])
        # Every capture-time write must land in a later second than every
        # seed-time write, or the two groups interleave differently per run.
        settle()
        corpus = str(paths["corpus"])
        approvals = {
            "list": approved_token(owner, "/local/list", {"path": corpus}),
            "read": approved_token(
                owner, "/local/read",
                {"path": str(paths["corpus"] / "ranking-notes.md")},
            ),
            "write": approved_token(
                owner, "/local/write",
                {"path": str(paths["corpus"] / "written.md"),
                 "content": "written by the fixture generator\n"},
            ),
            "folder": approved_token(
                owner, "/api/ingestion/folder",
                {"path": corpus, "recursive": True, "background": True},
            ),
            "watch": approved_token(
                owner, "/api/ingestion/watch",
                {"path": corpus, "recursive": True, "kind": "folder"},
            ),
            "tree": approved_token(
                owner, "/knowledge-graph/local/tree",
                {"path": corpus, "max_items": 20},
            ),
            "audit": approved_token(
                owner, "/knowledge-graph/local/audit",
                {"path": corpus, "max_files": 10},
            ),
        }

        rec = Recorder(normalizer)
        # settle() between phases: see its docstring — the graph reads order by
        # a second-resolution created_at, so phases must not share a second.
        chat_cases.capture_chat(rec, owner, anon, facts)
        chat_cases.capture_chat_history(rec, owner, anon, facts)
        chat_cases.capture_agent_http(rec, owner, member, anon)
        settle()
        # Persist *after* the chat phase: brain/memory/chronicle/command/
        # evidence fixtures were captured against that store (chat writes
        # conv-chat-* nodes and fixture-report.docx). Seed-only would miss them.
        persist_brain_store(paths["data"], replacements, f"{today}T12:00:00")
        if "--seed-store-only" in sys.argv:
            print(f"wrote {BRAIN_STORE}")
            print(f"wrote {BRAIN_STORE_DUMP}")
            return 0
        memory_cases.capture_memory(rec, owner, anon, facts)
        settle()
        memory_cases.capture_brain(rec, owner, anon)
        settle()
        memory_cases.capture_garden(rec, owner, anon, normalizer)
        settle()
        memory_cases.capture_chronicle(rec, owner, anon, today)
        memory_cases.capture_command_center(rec, owner, anon)
        memory_cases.capture_evidence_actions(rec, owner, anon, facts)
        knowledge_cases.capture_search(rec, owner, anon)
        knowledge_cases.capture_search_node(rec, owner, facts)
        settle()
        knowledge_cases.capture_knowledge_graph(rec, owner, member, anon, facts)
        settle()
        knowledge_cases.capture_index_jobs(rec, owner, anon)
        knowledge_cases.capture_local_files(rec, owner, anon, paths["corpus"], approvals)
        settle()
        knowledge_cases.capture_local_knowledge(rec, owner, anon, paths["corpus"], approvals, facts)
        settle()
        knowledge_cases.capture_browser(rec, owner, anon)
        # Destructive last: everything above reads what these remove.
        settle()
        chat_cases.capture_chat_history_destructive(rec, owner, anon, facts)
        memory_cases.capture_memory_destructive(rec, owner)
        settle()
        chat_cases.capture_chat_late(rec, owner)
        chat_cases.capture_chat_rate_limit(rec, burst)

        counts = write_files(rec.cases)
    finally:
        stack.close()
        shutil.rmtree(root, ignore_errors=True)

    total = sum(counts.values())
    print(f"wrote {total} cases to {OUT_DIR}")
    for name, count in sorted(counts.items()):
        print(f"  {name}.json: {count}")
    sse = sum(1 for case in rec.cases if "sse_frames" in case)
    frames = sum(len(case.get("sse_frames", [])) for case in rec.cases)
    print(f"  SSE captures: {sse} ({frames} frames)")
    print("gaps:")
    for gap in GAPS:
        print(f"  - {gap.splitlines()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
