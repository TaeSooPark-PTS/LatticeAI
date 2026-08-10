"""Interop bridges — other people's formats, through the one ingestion gate.

Through 11.1.0 Obsidian was the only external source with a bridge, and the
release said so plainly: Notion, email, calendar, and Git were *scoped out*
rather than stubbed. This module closes that gap, and it does it the same way
:mod:`latticeai.services.obsidian_bridge` does — by refusing to open a second
door into the graph.

Every bridge here:

* reads **local files the user already owns** (a Notion export, an ``.eml`` on
  disk, a repository path). Nothing calls a vendor API, nothing needs a token,
  and nothing leaves the machine;
* pushes every item through :meth:`IngestionPipeline.ingest`, so content
  hashing, hooks, provenance, extraction quality and workspace scoping are the
  ones the rest of the product already has;
* supports ``dry_run``, which reports exactly what a real run would touch and
  writes nothing;
* reports what it could **not** do — an unresolvable link, a missing decoder, a
  file it could not read — instead of quietly dropping it.

What is still out of scope, stated rather than implied: **system integration**.
There is no macOS Calendar / Mail permission dance, no IMAP, no Google
Calendar, no Notion API. Those need credentials and background sync, and the
honest version of this release is "point me at files you exported".
"""

from __future__ import annotations

import email
import email.policy
import json
import os
import re
import shutil
import subprocess  # noqa: S404 — one fixed binary, argv list, never a shell
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from lattice_brain.graph.ingest import _scoped_slug_id
from lattice_brain.ingestion import IngestionItem

# ── shared vocabulary ────────────────────────────────────────────────────────
SOURCE_NOTION = "notion"
SOURCE_GIT = "git_commit"
SOURCE_EMAIL = "email"
SOURCE_CALENDAR = "calendar_event"
#: Every bridge in this module, for a status surface that wants to list them.
BRIDGE_SOURCE_TYPES = (SOURCE_NOTION, SOURCE_GIT, SOURCE_EMAIL, SOURCE_CALENDAR)

LINK_RELATION = "REFERENCES"
TAG_RELATION = "TAGGED_AS"
TOPIC_NODE_TYPE = "Topic"

ERROR_REPORT_CAP = 25
UNRESOLVED_REPORT_CAP = 50
DEFAULT_MAX_ITEMS = 2000
DEFAULT_MAX_FILE_BYTES = 2_000_000
DEFAULT_GIT_COMMITS = 500

GRAPH_DISABLED_DETAIL = "Knowledge Graph ingestion is disabled (LATTICEAI_ENABLE_GRAPH)."


def record_error(errors: List[Dict[str, Any]], entry: Dict[str, Any]) -> None:
    """Append one failure to a capped report list.

    The *count* of failures is always exact; this list is a sample, so one
    unreadable directory cannot turn a summary into a megabyte of paths. One
    helper rather than three copies of the same ``if len(...) <`` check.
    """
    if len(errors) < ERROR_REPORT_CAP:
        errors.append(entry)


def edge_row(
    from_id: str, to_id: str, relation: str, metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """One ``import_graph_data`` edge row — the shape every bridge writes.

    Shared with :mod:`~latticeai.services.obsidian_bridge` on purpose: two
    copies of "what an edge row looks like" is two places to forget the
    ``metadata_json`` encoding.
    """
    return {
        "from_node": from_id,
        "to_node": to_id,
        "type": relation,
        "weight": 1.0,
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
    }


def topic_row(
    topic_id: str, label: str, *, summary: str, metadata: Dict[str, Any]
) -> Dict[str, Any]:
    """One ``Topic`` node row, identified by the store's own scoped slug."""
    return {
        "id": topic_id,
        "type": TOPIC_NODE_TYPE,
        "title": label,
        "summary": summary,
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
        "raw_json": "{}",
    }


@dataclass
class BridgeItem:
    """One thing a bridge found, before it reaches the pipeline."""

    key: str
    item: IngestionItem
    #: Keys of other items this one points at (resolved after the full scan).
    links: List[str] = field(default_factory=list)
    #: Free-text labels that become ``Topic`` nodes (file paths, calendars…).
    topics: List[str] = field(default_factory=list)


class InteropBridge:
    """Shared skeleton: scan → (dry_run?) → ingest → wire structure → report.

    Subclasses own exactly one thing — how to turn a local path into
    :class:`BridgeItem` values. Everything after that (the pipeline gate, the
    counters, the edge writes, the honest failure list) is identical across
    sources, which is the whole reason a second bridge did not become a second
    ingestion path.
    """

    source_type = "interop"
    label = "interop source"

    def __init__(
        self,
        *,
        pipeline: Any,
        knowledge_graph: Any = None,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self._pipeline = pipeline
        self._kg = knowledge_graph
        self._max_items = max(1, int(max_items))
        self._max_file_bytes = max(1, int(max_file_bytes))

    def available(self) -> bool:
        return self._pipeline is not None and bool(self._pipeline.available())

    # ── subclass seam ────────────────────────────────────────────────────────
    def scan(self, target: Any, **options: Any) -> Dict[str, Any]:
        """``{"status", "target", "items": [BridgeItem], "errors": [...] , …}``."""
        raise NotImplementedError  # pragma: no cover - abstract seam

    # ── the one public entry point ───────────────────────────────────────────
    def sync(
        self,
        target: Any,
        *,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        user_email: Optional[str] = None,
        dry_run: bool = False,
        **options: Any,
    ) -> Dict[str, Any]:
        """Ingest everything at ``target`` and wire whatever structure it has."""
        if not self.available():
            return {
                "status": "unavailable",
                "source": self.source_type,
                "target": str(target),
                "detail": GRAPH_DISABLED_DETAIL,
            }
        scan = self.scan(target, **options)
        summary: Dict[str, Any] = {
            "status": scan.get("status", "ok"),
            "source": self.source_type,
            "target": scan.get("target", str(target)),
            "dry_run": bool(dry_run),
            "scanned": int(scan.get("scanned") or 0),
            "items": len(scan.get("items") or []),
            "ingested": 0,
            "duplicate": 0,
            "failed": 0,
            "truncated": bool(scan.get("truncated")),
            "skipped": dict(scan.get("skipped") or {}),
            "links": {
                "resolved": 0,
                "written": 0,
                "unresolved_count": len(scan.get("unresolved") or []),
                "unresolved": list(scan.get("unresolved") or [])[:UNRESOLVED_REPORT_CAP],
            },
            "edges": {"status": "none", "references": 0, "topics": 0, "detail": None},
            "errors": list(scan.get("errors") or []),
        }
        if scan.get("status") != "ok":
            summary["status"] = "failed"
            summary["detail"] = scan.get("detail")
            return summary
        items: List[BridgeItem] = list(scan.get("items") or [])
        known = {entry.key for entry in items}
        resolved = {
            entry.key: [link for link in entry.links if link in known and link != entry.key]
            for entry in items
        }
        summary["links"]["resolved"] = sum(len(v) for v in resolved.values())
        summary["topics"] = len({topic for entry in items for topic in entry.topics})
        if dry_run:
            summary["status"] = "dry_run"
            return summary

        node_ids = self._ingest_items(items, summary=summary, user_email=user_email or owner)
        self._write_structure(
            items,
            node_ids=node_ids,
            resolved=resolved,
            owner=owner,
            workspace_id=workspace_id,
            summary=summary,
        )
        if summary["failed"] or summary["edges"]["status"] == "failed":
            summary["status"] = "partial"
        return summary

    # ── internals ────────────────────────────────────────────────────────────
    def _ingest_items(
        self,
        items: List[BridgeItem],
        *,
        summary: Dict[str, Any],
        user_email: Optional[str],
    ) -> Dict[str, str]:
        node_ids: Dict[str, str] = {}
        errors: List[Dict[str, Any]] = summary["errors"]
        for entry in items:
            result = self._pipeline.ingest(entry.item, user_email=user_email)
            if result.status != "ok":
                summary["failed"] += 1
                record_error(errors, {
                    "key": entry.key,
                    "status": result.status,
                    "detail": result.detail,
                })
                continue
            if result.duplicate:
                summary["duplicate"] += 1
            else:
                summary["ingested"] += 1
            if result.node_id:
                node_ids[entry.key] = result.node_id
        return node_ids

    def _write_structure(
        self,
        items: List[BridgeItem],
        *,
        node_ids: Dict[str, str],
        resolved: Dict[str, List[str]],
        owner: Optional[str],
        workspace_id: Optional[str],
        summary: Dict[str, Any],
    ) -> None:
        edges: List[Dict[str, Any]] = []
        topics: Dict[str, Dict[str, Any]] = {}
        for entry in items:
            from_id = node_ids.get(entry.key)
            if from_id is None:
                continue
            for target_key in resolved.get(entry.key, []):
                to_id = node_ids.get(target_key)
                if to_id is None:
                    continue
                edges.append(edge_row(from_id, to_id, LINK_RELATION, {
                    "source": self.source_type,
                    "from": entry.key,
                    "to": target_key,
                }))
            for label in entry.topics:
                topic_id = _scoped_slug_id("topic", label, workspace_id)
                topics.setdefault(topic_id, topic_row(
                    topic_id,
                    label,
                    summary=f"{self.label}: {label}",
                    metadata={
                        "topic": label,
                        "source": self.source_type,
                        "owner": owner,
                        "workspace_id": workspace_id,
                    },
                ))
                edges.append(edge_row(from_id, topic_id, TAG_RELATION, {
                    "source": self.source_type,
                    "topic": label,
                }))
        if not edges:
            return
        references = sum(1 for edge in edges if edge["type"] == LINK_RELATION)
        if self._kg is None:
            summary["edges"] = {
                "status": "skipped",
                "references": 0,
                "topics": 0,
                "detail": "no Knowledge Graph store is bound; items were ingested without relations",
            }
            return
        try:
            outcome = self._kg.import_graph_data(
                {
                    "nodes": list(topics.values()),
                    "edges": edges,
                    "chunks": [],
                    "knowledge_sources": [],
                    "provenance": [],
                },
                mode="merge",
            )
        except Exception as exc:  # noqa: BLE001 — items already landed; report, never crash
            summary["edges"] = {
                "status": "failed",
                "references": 0,
                "topics": 0,
                "detail": f"relations could not be written: {exc}",
            }
            return
        summary["links"]["written"] = references
        summary["edges"] = {
            "status": "written",
            "references": references,
            "topics": len(topics),
            "detail": None,
            "index": outcome.get("index"),
        }

    def _failed_scan(self, target: Any, detail: str) -> Dict[str, Any]:
        return {"status": "failed", "target": str(target), "detail": detail, "items": []}


# ── Notion export ────────────────────────────────────────────────────────────
NOTION_EXTENSIONS = frozenset({".md", ".markdown", ".csv"})
#: Notion appends a 32-hex page id to every exported filename. Two exports of
#: the same page produce two different ids, so the id is stripped from the
#: *title* and kept in metadata rather than shown to a reader.
_NOTION_ID_RE = re.compile(r"^(?P<title>.*?)[ _-]?(?P<page_id>[0-9a-f]{32})$", re.IGNORECASE)
_MD_LINK_RE = re.compile(r"\[[^\]\n]{0,200}\]\(([^()\s]{1,400})\)")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "notion://", "//")


def notion_title(stem: str) -> Tuple[str, Optional[str]]:
    """``("Roadmap", "1a2b…")`` from ``"Roadmap 1a2b…"`` — id split off, not lost."""
    match = _NOTION_ID_RE.match(str(stem or "").strip())
    if match is None:
        return str(stem or "").strip(), None
    title = match.group("title").strip()
    page_id = match.group("page_id").lower()
    return (title or page_id), page_id


def notion_key(relative_path: str) -> str:
    """Stable identity for one exported page: its path with the id stripped."""
    path = PurePath(str(relative_path or "").replace("\\", "/"))
    title, _ = notion_title(path.stem)
    parent = "/".join(part for part in path.parent.parts if part not in (".", ""))
    normalized = f"{parent}/{title}" if parent else title
    return normalized.strip("/").lower()


def notion_links(body: str) -> List[str]:
    """Relative page links inside an exported page, in document order."""
    found: List[str] = []
    seen: set = set()
    for match in _MD_LINK_RE.finditer(str(body or "")):
        raw = unquote(match.group(1)).strip()
        if not raw or raw.startswith(_EXTERNAL_PREFIXES):
            continue
        suffix = PurePath(raw).suffix.lower()
        if suffix and suffix not in NOTION_EXTENSIONS:
            continue
        key = notion_key(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


class NotionExportBridge(InteropBridge):
    """A Notion **export** (directory or ``.zip``) through the one gate.

    Deliberately not the Notion API: an API bridge needs an integration token,
    a network round trip per page, and a background sync to stay current — all
    of which are the opposite of "local-first, opt-in, off by default". An
    export is a folder the user already downloaded, and it contains the same
    words.
    """

    source_type = SOURCE_NOTION
    label = "Notion page"

    def scan(self, target: Any, **options: Any) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "status": "ok",
            "target": str(target),
            "scanned": 0,
            "items": [],
            "skipped": {"empty": 0, "too_large": 0, "unreadable": 0},
            "truncated": False,
            "errors": [],
        }
        try:
            root = Path(target).expanduser()
        except TypeError:
            return self._failed_scan(target, f"invalid export path: {target!r}")
        if root.is_file() and root.suffix.lower() == ".zip":
            return self._scan_zip(root, report)
        if not root.is_dir():
            return self._failed_scan(root, f"not a Notion export directory or zip: {root}")
        report["target"] = str(root)
        self._walk(root, report)
        return report

    def _scan_zip(self, archive: Path, report: Dict[str, Any]) -> Dict[str, Any]:
        """Read a ``.zip`` export by extracting it to a temp dir first.

        A zip member is not a path the pipeline can hash and re-read later, so
        the export is materialized once and the ordinary directory walk runs
        over it. Unsafe member names are refused outright.
        """
        try:
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
                for name in names:
                    member = PurePath(name.replace("\\", "/"))
                    if member.is_absolute() or ".." in member.parts:
                        return self._failed_scan(
                            archive, f"export archive contains an unsafe path: {name}"
                        )
                staging = Path(tempfile.mkdtemp(prefix="notion-export-"))
                zf.extractall(staging)
        except (zipfile.BadZipFile, OSError) as exc:
            return self._failed_scan(archive, f"export archive could not be read: {exc}")
        report["target"] = str(archive)
        report["extracted_to"] = str(staging)
        self._walk(staging, report)
        return report

    def _walk(self, root: Path, report: Dict[str, Any]) -> None:
        items: List[BridgeItem] = report["items"]
        skipped = report["skipped"]
        errors: List[Dict[str, Any]] = report["errors"]
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
            current = Path(dirpath)
            for name in sorted(filenames):
                if name.startswith(".") or Path(name).suffix.lower() not in NOTION_EXTENSIONS:
                    continue
                report["scanned"] += 1
                path = current / name
                if len(items) >= self._max_items:
                    report["truncated"] = True
                    continue
                try:
                    if path.stat().st_size > self._max_file_bytes:
                        skipped["too_large"] += 1
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    skipped["unreadable"] += 1
                    record_error(errors, {"key": name, "status": "unreadable", "detail": str(exc)})
                    continue
                if not text.strip():
                    skipped["empty"] += 1
                    continue
                relative = path.relative_to(root).as_posix()
                title, page_id = notion_title(path.stem)
                items.append(BridgeItem(
                    key=notion_key(relative),
                    links=notion_links(text),
                    item=IngestionItem(
                        source_type=SOURCE_NOTION,
                        title=title,
                        text=text,
                        source_uri=str(path),
                        mime_type="text/markdown" if path.suffix.lower() != ".csv" else "text/csv",
                        metadata={
                            "relative_path": relative,
                            "notion_page_id": page_id,
                            "export_kind": "database" if path.suffix.lower() == ".csv" else "page",
                        },
                    ),
                ))


# ── Git repository ───────────────────────────────────────────────────────────
GIT_BINARY = "git"
_GIT_RECORD_SEPARATOR = "\x00"
_GIT_FIELD_SEPARATOR = "\x1f"
_GIT_PRETTY = (
    f"format:{_GIT_RECORD_SEPARATOR}%H{_GIT_FIELD_SEPARATOR}%an{_GIT_FIELD_SEPARATOR}"
    f"%ae{_GIT_FIELD_SEPARATOR}%aI{_GIT_FIELD_SEPARATOR}%s{_GIT_FIELD_SEPARATOR}%b"
    f"{_GIT_RECORD_SEPARATOR}"
)
GIT_UNAVAILABLE_DETAIL = (
    "reading a repository's history needs git on this machine and none was "
    "found; nothing was ingested"
)
GIT_TIMEOUT_SECONDS = 60


def _which_git() -> Optional[str]:
    """Absolute path to git, or ``None``. The one probe, seamed for tests."""
    return shutil.which(GIT_BINARY)


def _run_git(binary: str, args: List[str], cwd: Path) -> Tuple[int, str]:
    """Run git with an argv list in a fixed directory — no shell, ever."""
    completed = subprocess.run(  # noqa: S603 — argv list, fixed binary, no shell
        [binary, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    return int(completed.returncode), str(completed.stdout or "")


def parse_git_log(output: str) -> List[Dict[str, Any]]:
    """Parse the ``--pretty``/``--name-only`` stream into commit records.

    The separators are NUL and unit-separator rather than newlines because a
    commit body contains newlines and a filename may contain almost anything
    else. Anything that does not parse into six fields is skipped rather than
    guessed at.
    """
    chunks = str(output or "").split(_GIT_RECORD_SEPARATOR)
    commits: List[Dict[str, Any]] = []
    index = 1
    while index < len(chunks):
        fields = chunks[index].split(_GIT_FIELD_SEPARATOR)
        trailer = chunks[index + 1] if index + 1 < len(chunks) else ""
        index += 2
        if len(fields) != 6:
            continue
        sha, author, mail, when, subject, body = fields
        files = [line.strip() for line in trailer.splitlines() if line.strip()]
        commits.append({
            "sha": sha.strip(),
            "author": author.strip(),
            "author_email": mail.strip(),
            "date": when.strip(),
            "subject": subject.strip(),
            "body": body.strip(),
            "files": files,
        })
    return commits


class GitHistoryBridge(InteropBridge):
    """A local repository's commit history as project memory.

    One node per commit — message, author, date, and the files it touched —
    with each file path joined as a ``Topic``, so "what changed in the auth
    module" is a graph traversal rather than a shell command. The commit hash
    rides in the source URI and the body, so re-running reports duplicates
    instead of writing a second copy of the same commit.

    Nothing is cloned or fetched: this reads a path the user approved, with
    ``git log``, and says so when git is not installed.
    """

    source_type = SOURCE_GIT
    label = "repository file"

    def scan(self, target: Any, **options: Any) -> Dict[str, Any]:
        limit = max(1, int(options.get("max_commits") or DEFAULT_GIT_COMMITS))
        report: Dict[str, Any] = {
            "status": "ok",
            "target": str(target),
            "scanned": 0,
            "items": [],
            "truncated": False,
            "errors": [],
        }
        try:
            root = Path(target).expanduser()
        except TypeError:
            return self._failed_scan(target, f"invalid repository path: {target!r}")
        if not root.is_dir():
            return self._failed_scan(root, f"not a directory: {root}")
        if not (root / ".git").exists():
            return self._failed_scan(root, f"not a git repository (no .git): {root}")
        binary = _which_git()
        if binary is None:
            return self._failed_scan(root, GIT_UNAVAILABLE_DETAIL)
        args = [
            "log", f"--max-count={limit}", "--name-only",
            f"--pretty={_GIT_PRETTY}",
        ]
        try:
            code, output = _run_git(binary, args, root)
        except Exception as exc:  # noqa: BLE001 — a broken git is a state, not a crash
            return self._failed_scan(root, f"git log failed: {exc}")
        if code != 0:
            return self._failed_scan(root, f"git log exited with status {code}")
        report["target"] = str(root)
        commits = parse_git_log(output)
        report["scanned"] = len(commits)
        items: List[BridgeItem] = report["items"]
        for commit in commits:
            if len(items) >= self._max_items:
                report["truncated"] = True
                break
            items.append(self._commit_item(root, commit))
        return report

    def _commit_item(self, root: Path, commit: Dict[str, Any]) -> BridgeItem:
        sha = commit["sha"]
        files = commit["files"]
        lines = [
            f"commit {sha}",
            f"작성자: {commit['author']} <{commit['author_email']}>",
            f"시각: {commit['date']}",
            "",
            commit["subject"],
        ]
        if commit["body"]:
            lines.extend(["", commit["body"]])
        if files:
            lines.extend(["", "변경된 파일:", *[f"- {name}" for name in files]])
        return BridgeItem(
            key=sha,
            topics=list(files),
            item=IngestionItem(
                source_type=SOURCE_GIT,
                title=f"{commit['subject'] or sha[:12]} ({sha[:8]})",
                text="\n".join(lines),
                source_uri=f"git:{root}#{sha}",
                mime_type="text/plain",
                modified_at=commit["date"] or None,
                metadata={
                    "repository": str(root),
                    "commit": sha,
                    "author": commit["author"],
                    "author_email": commit["author_email"],
                    "committed_at": commit["date"],
                    "files": files,
                    "file_count": len(files),
                },
            ),
        )


# ── email (.eml) and calendar (.ics) ─────────────────────────────────────────
EMAIL_EXTENSIONS = frozenset({".eml"})
CALENDAR_EXTENSIONS = frozenset({".ics"})
MAILBOX_EXTENSIONS = EMAIL_EXTENSIONS | CALENDAR_EXTENSIONS
_ICS_ESCAPES = (("\\n", "\n"), ("\\N", "\n"), ("\\,", ","), ("\\;", ";"), ("\\\\", "\\"))


def _unfold_ics(text: str) -> List[str]:
    """RFC 5545 line unfolding: a leading space continues the previous line."""
    lines: List[str] = []
    for raw in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
            continue
        lines.append(raw)
    return lines


def _ics_value(raw: str) -> str:
    value = raw
    for token, replacement in _ICS_ESCAPES:
        value = value.replace(token, replacement)
    return value.strip()


def parse_ics(text: str) -> List[Dict[str, str]]:
    """Every ``VEVENT`` in an ``.ics`` file as a flat dict of its properties.

    A deliberately small parser rather than a dependency: this needs five
    properties out of a format whose grammar is line folding plus
    ``NAME;PARAM=…:VALUE``, and a calendar library in the ingest path would be
    a permanent cost for that. Properties it does not understand are ignored,
    never guessed at, and an unterminated block is dropped rather than
    half-reported.
    """
    events: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None
    for line in _unfold_ics(text):
        stripped = line.strip()
        if stripped.upper() == "BEGIN:VEVENT":
            current = {}
            continue
        if stripped.upper() == "END:VEVENT":
            # A stray END with no BEGIN closes nothing rather than inventing an
            # empty event.
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in stripped:
            continue
        name, _, value = stripped.partition(":")
        key = name.split(";", 1)[0].strip().upper()
        current[key] = _ics_value(value)
    return events


def _preferred_part(message: Any, kind: str) -> Any:
    """One ``get_body`` lookup; a malformed message answers ``None``, not a raise."""
    try:
        return message.get_body(preferencelist=(kind,))
    except Exception:  # noqa: BLE001 — a malformed message is a state
        return None


def email_body(message: Any) -> Tuple[str, str]:
    """``(body, status)`` for a parsed message — plain text only, honestly.

    HTML-only mail yields an empty body and ``html_only``: turning markup into
    prose is a job for the extraction layer the capture surfaces already own,
    and a naive tag-strip here would store navigation chrome as if it were the
    message.
    """
    part = _preferred_part(message, "plain")
    if part is None:
        return "", "html_only" if _preferred_part(message, "html") else "empty"
    try:
        content = str(part.get_content() or "").strip()
    except Exception as exc:  # noqa: BLE001 — undecodable charset is a state
        return "", f"undecodable: {exc}"
    return content, "ok" if content else "empty"


class MailCalendarBridge(InteropBridge):
    """Local ``.eml`` messages and ``.ics`` calendars, through the one gate.

    Both are file formats with standard-library (or five-line) parsers, which
    is exactly why they are in this release and a live mailbox connection is
    not: reading ``~/Mail`` needs an OS permission grant and a sync loop, and
    neither belongs behind a feature that claims to only read what it was
    pointed at. Point this at a folder of exported messages.
    """

    source_type = SOURCE_EMAIL
    label = "calendar"

    def scan(self, target: Any, **options: Any) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "status": "ok",
            "target": str(target),
            "scanned": 0,
            "items": [],
            "skipped": {"empty": 0, "too_large": 0, "unreadable": 0},
            "truncated": False,
            "errors": [],
        }
        try:
            root = Path(target).expanduser()
        except TypeError:
            return self._failed_scan(target, f"invalid path: {target!r}")
        if root.is_file():
            paths = [root] if root.suffix.lower() in MAILBOX_EXTENSIONS else []
            if not paths:
                return self._failed_scan(root, f"not an .eml or .ics file: {root}")
        elif root.is_dir():
            paths = sorted(
                path for path in root.rglob("*")
                if path.is_file()
                and not path.name.startswith(".")
                and path.suffix.lower() in MAILBOX_EXTENSIONS
            )
        else:
            return self._failed_scan(root, f"no such file or folder: {root}")
        report["target"] = str(root)
        self._read_all(paths, report)
        return report

    def _read_all(self, paths: List[Path], report: Dict[str, Any]) -> None:
        items: List[BridgeItem] = report["items"]
        skipped = report["skipped"]
        errors: List[Dict[str, Any]] = report["errors"]
        for path in paths:
            report["scanned"] += 1
            if len(items) >= self._max_items:
                report["truncated"] = True
                continue
            try:
                if path.stat().st_size > self._max_file_bytes:
                    skipped["too_large"] += 1
                    continue
                raw = path.read_bytes()
            except OSError as exc:
                skipped["unreadable"] += 1
                record_error(errors, {"key": path.name, "status": "unreadable", "detail": str(exc)})
                continue
            produced = (
                self._calendar_items(path, raw)
                if path.suffix.lower() in CALENDAR_EXTENSIONS
                else self._message_items(path, raw)
            )
            if not produced:
                skipped["empty"] += 1
                continue
            items.extend(produced)

    def _message_items(self, path: Path, raw: bytes) -> List[BridgeItem]:
        try:
            message = email.message_from_bytes(raw, policy=email.policy.default)
        except Exception as exc:  # noqa: BLE001 — an unparseable message is a state
            return [self._unreadable_item(path, f"message could not be parsed: {exc}")]
        body, status = email_body(message)
        subject = str(message.get("Subject") or path.stem).strip() or path.stem
        sender = str(message.get("From") or "").strip()
        recipients = str(message.get("To") or "").strip()
        sent_at = str(message.get("Date") or "").strip()
        message_id = str(message.get("Message-ID") or "").strip()
        header_block = "\n".join(
            line for line in (
                f"보낸 사람: {sender}" if sender else "",
                f"받는 사람: {recipients}" if recipients else "",
                f"보낸 시각: {sent_at}" if sent_at else "",
            ) if line
        )
        text = f"{header_block}\n\n{body}".strip() if body else (
            f"{header_block}\n\n[본문 없음] 이 메일은 일반 텍스트 본문이 없어 "
            "내용 검색은 되지 않습니다."
        ).strip()
        return [BridgeItem(
            key=message_id or str(path),
            item=IngestionItem(
                source_type=SOURCE_EMAIL,
                title=subject,
                text=text,
                source_uri=str(path),
                mime_type="message/rfc822",
                modified_at=sent_at or None,
                metadata={
                    "from": sender,
                    "to": recipients,
                    "sent_at": sent_at,
                    "message_id": message_id,
                    "body_status": status,
                    "searchable": bool(body),
                },
            ),
        )]

    def _calendar_items(self, path: Path, raw: bytes) -> List[BridgeItem]:
        events = parse_ics(raw.decode("utf-8", errors="ignore"))
        produced: List[BridgeItem] = []
        for index, event in enumerate(events):
            title = event.get("SUMMARY") or f"{path.stem} 일정 {index + 1}"
            uid = event.get("UID") or f"{path}#{index}"
            lines = [title]
            for label, key in (("시작", "DTSTART"), ("종료", "DTEND"), ("장소", "LOCATION")):
                if event.get(key):
                    lines.append(f"{label}: {event[key]}")
            if event.get("DESCRIPTION"):
                lines.extend(["", event["DESCRIPTION"]])
            produced.append(BridgeItem(
                key=uid,
                topics=[event["LOCATION"]] if event.get("LOCATION") else [],
                item=IngestionItem(
                    source_type=SOURCE_CALENDAR,
                    title=title,
                    text="\n".join(lines),
                    source_uri=f"{path}#{uid}",
                    mime_type="text/calendar",
                    modified_at=event.get("DTSTART") or None,
                    metadata={
                        "calendar_file": str(path),
                        "uid": uid,
                        "starts_at": event.get("DTSTART", ""),
                        "ends_at": event.get("DTEND", ""),
                        "location": event.get("LOCATION", ""),
                    },
                ),
            ))
        return produced

    @staticmethod
    def _unreadable_item(path: Path, detail: str) -> BridgeItem:
        """Keep the fact that a file existed, and say what went wrong with it."""
        return BridgeItem(
            key=str(path),
            item=IngestionItem(
                source_type=SOURCE_EMAIL,
                title=path.stem,
                text=f"[읽을 수 없는 메일] {path.name}\n{detail}",
                source_uri=str(path),
                mime_type="message/rfc822",
                metadata={"body_status": "unreadable", "detail": detail, "searchable": False},
            ),
        )


BRIDGES: Dict[str, Any] = {
    SOURCE_NOTION: NotionExportBridge,
    "git": GitHistoryBridge,
    "mail": MailCalendarBridge,
}


def build_bridge(
    kind: str, *, pipeline: Any, knowledge_graph: Any = None, **options: Any
) -> InteropBridge:
    """One named bridge, or a ``ValueError`` naming the ones that exist."""
    factory = BRIDGES.get(str(kind or "").strip().lower())
    if factory is None:
        raise ValueError(
            f"unknown interop source '{kind}'; available: {', '.join(sorted(BRIDGES))}"
        )
    return factory(pipeline=pipeline, knowledge_graph=knowledge_graph, **options)


def bridge_status() -> Dict[str, Any]:
    """What each bridge can do on *this* machine, right now.

    Git is the only one with a runtime prerequisite, and it is reported rather
    than discovered when someone tries to use it.
    """
    return {
        "sources": {
            SOURCE_NOTION: {
                "available": True,
                "accepts": ["directory", ".zip"],
                "detail": "a Notion export you downloaded — never the Notion API",
            },
            "git": {
                "available": _which_git() is not None,
                "accepts": ["repository directory"],
                "detail": None if _which_git() else GIT_UNAVAILABLE_DETAIL,
            },
            "mail": {
                "available": True,
                "accepts": [".eml", ".ics", "a folder of either"],
                "detail": (
                    "local files only; connecting a live mailbox or system "
                    "calendar is deliberately out of scope"
                ),
            },
        },
    }


__all__ = [
    "BRIDGES",
    "BRIDGE_SOURCE_TYPES",
    "CALENDAR_EXTENSIONS",
    "DEFAULT_GIT_COMMITS",
    "EMAIL_EXTENSIONS",
    "GIT_UNAVAILABLE_DETAIL",
    "NOTION_EXTENSIONS",
    "SOURCE_CALENDAR",
    "SOURCE_EMAIL",
    "SOURCE_GIT",
    "SOURCE_NOTION",
    "BridgeItem",
    "GitHistoryBridge",
    "InteropBridge",
    "MailCalendarBridge",
    "NotionExportBridge",
    "bridge_status",
    "build_bridge",
    "edge_row",
    "email_body",
    "notion_key",
    "notion_links",
    "notion_title",
    "parse_git_log",
    "record_error",
    "parse_ics",
    "topic_row",
]
