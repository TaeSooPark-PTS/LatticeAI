"""Obsidian vault bridge — an *external* vault through the one ingestion gate.

Not to be confused with ``latticeai/tools/knowledge.py`` (``obsidian_save`` /
``obsidian_search``), which writes Lattice's *own* mirror vault under the brain
directory. This module reads a vault the user already owns — a folder full of
``.md`` notes written by Obsidian — and pushes every note through the same
:class:`~lattice_brain.ingestion.IngestionPipeline` door as files, folders,
web captures, and chat: content hashing, hooks, provenance, extraction quality.
No second write path into the graph, no silo.

What the bridge adds on top of a plain folder ingest is the vault's *structure*:

* ``[[wikilinks]]`` (including ``[[target|alias]]``, ``[[target#heading]]``,
  and ``![[embeds]]``) plus relative markdown links between notes become real
  ``REFERENCES`` edges between the ingested note nodes, so a backlink in
  Obsidian is a traversable relation in the Brain.
* Frontmatter ``tags`` become ``Topic`` nodes joined by ``TAGGED_AS``, sharing
  the store's own scoped-slug identity so a vault tag and an extracted topic of
  the same name are one node, not two.
* A link whose target does not exist in the vault (or is ambiguous between two
  notes of the same basename) is **reported**, never invented. The pending list
  is part of the summary.

Direct edge writes are legitimate here because a vault sync is user-initiated
ingestion, not an agent proposal — the same rule that lets ``/api/ingestion/
folder`` write. Re-running is idempotent: content-hash dedup is the pipeline's
job, and edge/topic ids are deterministic, so a second sync updates rather than
duplicates.

Scope (v11.1.0): manual one-shot sync only. There is no watch mode and no
background scheduling for external vaults — link edges need the node ids that
only a completed inline ingest has, and a deferred edge that never lands would
be a lie in the summary.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

# ``_scoped_slug_id`` is the store's own scoped-id helper. Importing it rather
# than re-deriving the slug rules is what makes a vault tag and a
# store-extracted topic the same node instead of two nodes with one label.
from lattice_brain.graph.ingest import _scoped_slug_id
from lattice_brain.ingestion import IngestionItem
from latticeai.services.interop_bridges import edge_row, topic_row

SOURCE_TYPE = "obsidian"
NOTE_EXTENSIONS = frozenset({".md", ".markdown"})
#: Directories never walked. Hidden entries (``.obsidian``, ``.trash``,
#: ``.git``) are skipped by the leading-dot rule; these are the visible ones.
SKIP_DIRS = frozenset({"node_modules", "__pycache__"})
DEFAULT_MAX_FILES = 2000
DEFAULT_MAX_FILE_BYTES = 2_000_000
#: Vault link → graph relation. ``REFERENCES`` is the store's canonical
#: FILE → FILE / URL edge, so no new taxonomy is minted here.
LINK_RELATION = "REFERENCES"
TAG_RELATION = "TAGGED_AS"
TOPIC_NODE_TYPE = "Topic"
UNRESOLVED_REPORT_CAP = 50
ERROR_REPORT_CAP = 25

_WIKILINK_RE = re.compile(r"!?\[\[([^\[\]]{1,300}?)\]\]")
_MD_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]{0,200}\]\(([^()\s]{1,300})\)")
_EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "obsidian://", "//")


@dataclass
class VaultNote:
    """One parsed note, before it reaches the pipeline."""

    path: Path
    relative_path: str
    title: str
    body: str
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    #: Raw link targets exactly as written in the note.
    link_targets: List[str] = field(default_factory=list)


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split a leading ``---`` YAML block off a note.

    Deliberately a *minimal* YAML subset — ``key: value``, ``key: [a, b]``,
    and ``- item`` continuation lines — because the only thing this bridge
    needs from frontmatter is tags, and pulling a YAML dependency in for that
    would put a parser in the ingest path for no recall gain. Anything the
    subset does not understand is left in the block and ignored, never
    guessed at.
    """
    raw = str(text or "")
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() in {"---", "..."}:
            closing = index
            break
    if closing is None:
        return {}, raw
    data: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for line in lines[1:closing]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if current_key is not None:
                bucket = data.setdefault(current_key, [])
                if isinstance(bucket, list):
                    bucket.append(_scalar(stripped[2:]))
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        current_key = key.strip()
        rest = value.strip()
        if not rest:
            data[current_key] = []
            continue
        data[current_key] = _yaml_value(rest)
    body = "\n".join(lines[closing + 1:])
    return data, body


def _scalar(value: str) -> str:
    return str(value).strip().strip("'\"").strip()


def _yaml_value(value: str) -> Any:
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        return [_scalar(part) for part in text[1:-1].split(",") if _scalar(part)]
    if "," in text:
        return [_scalar(part) for part in text.split(",") if _scalar(part)]
    return _scalar(text)


def frontmatter_tags(frontmatter: Dict[str, Any]) -> List[str]:
    """Tags from a parsed frontmatter block, de-duplicated case-insensitively."""
    collected: List[str] = []
    seen: set = set()
    for key in ("tags", "tag"):
        value = frontmatter.get(key)
        if value is None:
            continue
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            tag = str(candidate or "").strip().lstrip("#").strip()
            if not tag or tag.lower() in seen:
                continue
            seen.add(tag.lower())
            collected.append(tag)
    return collected


def extract_link_targets(body: str) -> List[str]:
    """Wikilink and relative-markdown-link targets, in document order.

    Aliases (``|``), headings (``#``), and block refs (``^``) are stripped so
    ``[[Design#Rationale|why]]`` resolves to the note ``Design``. External
    URLs are not vault links and are dropped.
    """
    targets: List[str] = []
    seen: set = set()

    def _add(raw: str) -> None:
        target = _normalize_target(raw)
        if not target or target.lower() in seen:
            return
        seen.add(target.lower())
        targets.append(target)

    for match in _WIKILINK_RE.finditer(body or ""):
        _add(match.group(1).split("|", 1)[0])
    for match in _MD_LINK_RE.finditer(body or ""):
        _add(unquote(match.group(1)))
    return targets


def _normalize_target(raw: str) -> str:
    target = str(raw or "").strip()
    if not target or target.startswith(_EXTERNAL_PREFIXES):
        return ""
    target = target.split("#", 1)[0].split("^", 1)[0].strip()
    if not target:
        return ""
    suffix = Path(target).suffix.lower()
    # A relative link to an image or a PDF is an attachment, not a note.
    if suffix and suffix not in NOTE_EXTENSIONS:
        return ""
    return target


def _index_key(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    lowered = text.lower()
    for extension in NOTE_EXTENSIONS:
        if lowered.endswith(extension):
            return lowered[: -len(extension)]
    return lowered


class ObsidianVaultBridge:
    """Reads an approved external vault and feeds it to the ingestion gate."""

    def __init__(
        self,
        *,
        pipeline: Any,
        knowledge_graph: Any = None,
        max_files: int = DEFAULT_MAX_FILES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        self._pipeline = pipeline
        self._kg = knowledge_graph
        self._max_files = max(1, int(max_files))
        self._max_file_bytes = max(1, int(max_file_bytes))

    def available(self) -> bool:
        return self._pipeline is not None and bool(self._pipeline.available())

    # ── scanning ─────────────────────────────────────────────────────────────
    def scan(self, vault_path: Any) -> Dict[str, Any]:
        """Walk a vault into parsed notes plus a resolved link graph.

        Pure read: no ingest, no graph write. ``sync(dry_run=True)`` is this
        with counts, which is what the API returns before anyone commits.
        """
        report: Dict[str, Any] = {
            "vault": str(vault_path),
            "scanned": 0,
            "notes": [],
            "skipped": {"empty": 0, "too_large": 0, "unreadable": 0},
            "truncated": False,
            "errors": [],
        }
        try:
            root = Path(vault_path).expanduser()
        except TypeError:
            report["status"] = "failed"
            report["detail"] = f"invalid vault path: {vault_path!r}"
            return report
        if not root.is_dir():
            report["status"] = "failed"
            report["detail"] = f"not a vault directory: {root}"
            return report
        report["vault"] = str(root)
        notes: List[VaultNote] = self._collect(root, report)
        report["notes"] = notes
        report["status"] = "ok"
        return report

    def _collect(self, root: Path, report: Dict[str, Any]) -> List[VaultNote]:
        notes: List[VaultNote] = []
        skipped = report["skipped"]
        errors: List[Dict[str, Any]] = report["errors"]
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                name for name in dirnames
                if not name.startswith(".") and name not in SKIP_DIRS
            )
            current = Path(dirpath)
            for name in sorted(filenames):
                if Path(name).suffix.lower() not in NOTE_EXTENSIONS:
                    continue
                if name.startswith("."):
                    continue
                report["scanned"] += 1
                path = current / name
                if len(notes) >= self._max_files:
                    report["truncated"] = True
                    continue
                try:
                    size = path.stat().st_size
                    if size > self._max_file_bytes:
                        skipped["too_large"] += 1
                        continue
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    skipped["unreadable"] += 1
                    if len(errors) < ERROR_REPORT_CAP:
                        errors.append({"path": str(path), "status": "unreadable", "detail": str(exc)})
                    continue
                frontmatter, body = parse_frontmatter(text)
                if not body.strip():
                    # A note that is only frontmatter has nothing to recall.
                    skipped["empty"] += 1
                    continue
                notes.append(
                    VaultNote(
                        path=path,
                        relative_path=path.relative_to(root).as_posix(),
                        title=str(frontmatter.get("title") or path.stem),
                        body=body,
                        frontmatter=frontmatter,
                        tags=frontmatter_tags(frontmatter),
                        link_targets=extract_link_targets(body),
                    )
                )
        return notes

    # ── link resolution ──────────────────────────────────────────────────────
    @staticmethod
    def resolve_links(notes: List[VaultNote]) -> Tuple[Dict[str, List[str]], List[Dict[str, str]]]:
        """``(relative_path → linked relative paths, unresolved reports)``.

        Exact relative path wins; otherwise a unique basename wins (Obsidian's
        own shortest-path rule). A basename shared by two notes is reported as
        ``ambiguous`` rather than guessed, and a target that matches nothing is
        reported as ``missing``.
        """
        by_path: Dict[str, str] = {}
        by_name: Dict[str, List[str]] = {}
        for note in notes:
            by_path[_index_key(note.relative_path)] = note.relative_path
            by_name.setdefault(_index_key(Path(note.relative_path).name), []).append(
                note.relative_path
            )
        resolved: Dict[str, List[str]] = {}
        unresolved: List[Dict[str, str]] = []
        for note in notes:
            links: List[str] = []
            for raw in note.link_targets:
                key = _index_key(raw)
                target = by_path.get(key)
                if target is None:
                    candidates = by_name.get(key) or []
                    if len(candidates) == 1:
                        target = candidates[0]
                    else:
                        unresolved.append({
                            "from": note.relative_path,
                            "target": raw,
                            "reason": "ambiguous" if candidates else "missing",
                        })
                        continue
                if target != note.relative_path and target not in links:
                    links.append(target)
            resolved[note.relative_path] = links
        return resolved, unresolved

    # ── sync ─────────────────────────────────────────────────────────────────
    def sync(
        self,
        vault_path: Any,
        *,
        owner: Optional[str] = None,
        workspace_id: Optional[str] = None,
        user_email: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Ingest every note in an approved vault, then wire its structure.

        Returns a summary the API hands back verbatim. ``dry_run`` reports what
        a real run would touch — note count, resolvable links, tags, and the
        pending list — without a single write.
        """
        if not self.available():
            return {
                "status": "unavailable",
                "vault": str(vault_path),
                "detail": "Knowledge Graph ingestion is disabled (LATTICEAI_ENABLE_GRAPH).",
            }
        scan = self.scan(vault_path)
        if scan.get("status") != "ok":
            return {
                "status": "failed",
                "vault": scan["vault"],
                "detail": scan.get("detail"),
                "scanned": scan["scanned"],
            }
        notes: List[VaultNote] = scan["notes"]
        resolved, unresolved = self.resolve_links(notes)
        tags = sorted({tag for note in notes for tag in note.tags}, key=str.lower)
        backlinks: Dict[str, List[str]] = {note.relative_path: [] for note in notes}
        for source, targets in resolved.items():
            for target in targets:
                backlinks[target].append(source)

        summary: Dict[str, Any] = {
            "status": "ok",
            "vault": scan["vault"],
            "dry_run": bool(dry_run),
            "scanned": scan["scanned"],
            "notes": len(notes),
            "ingested": 0,
            "duplicate": 0,
            "failed": 0,
            "skipped": scan["skipped"],
            "truncated": scan["truncated"],
            "tags": len(tags),
            "links": {
                "resolved": sum(len(targets) for targets in resolved.values()),
                "written": 0,
                "unresolved_count": len(unresolved),
                "unresolved": unresolved[:UNRESOLVED_REPORT_CAP],
            },
            "edges": {"status": "none", "references": 0, "tags": 0, "detail": None},
            "errors": list(scan["errors"]),
        }
        if dry_run:
            summary["status"] = "dry_run"
            return summary

        node_ids = self._ingest_notes(
            notes,
            vault=scan["vault"],
            resolved=resolved,
            backlinks=backlinks,
            owner=owner,
            workspace_id=workspace_id,
            user_email=user_email,
            summary=summary,
        )
        self._write_structure(
            notes,
            node_ids=node_ids,
            resolved=resolved,
            vault=scan["vault"],
            owner=owner,
            workspace_id=workspace_id,
            summary=summary,
        )
        if summary["failed"] or summary["edges"]["status"] == "failed":
            summary["status"] = "partial"
        return summary

    def _ingest_notes(
        self,
        notes: List[VaultNote],
        *,
        vault: str,
        resolved: Dict[str, List[str]],
        backlinks: Dict[str, List[str]],
        owner: Optional[str],
        workspace_id: Optional[str],
        user_email: Optional[str],
        summary: Dict[str, Any],
    ) -> Dict[str, str]:
        node_ids: Dict[str, str] = {}
        errors: List[Dict[str, Any]] = summary["errors"]
        for note in notes:
            item = IngestionItem(
                source_type=SOURCE_TYPE,
                title=note.title,
                text=note.body,
                source_uri=str(note.path),
                mime_type="text/markdown",
                owner=owner,
                workspace_id=workspace_id,
                metadata={
                    "vault": vault,
                    "relative_path": note.relative_path,
                    "frontmatter": note.frontmatter,
                    "tags": note.tags,
                    "wikilinks": note.link_targets,
                    "links": resolved.get(note.relative_path, []),
                    "backlinks": backlinks.get(note.relative_path, []),
                },
            )
            result = self._pipeline.ingest(item, user_email=user_email or owner)
            if result.status != "ok":
                summary["failed"] += 1
                if len(errors) < ERROR_REPORT_CAP:
                    errors.append({
                        "path": note.relative_path,
                        "status": result.status,
                        "detail": result.detail,
                    })
                continue
            if result.duplicate:
                summary["duplicate"] += 1
            else:
                summary["ingested"] += 1
            if result.node_id:
                node_ids[note.relative_path] = result.node_id
        return node_ids

    def _write_structure(
        self,
        notes: List[VaultNote],
        *,
        node_ids: Dict[str, str],
        resolved: Dict[str, List[str]],
        vault: str,
        owner: Optional[str],
        workspace_id: Optional[str],
        summary: Dict[str, Any],
    ) -> None:
        """Write link + tag edges through the store's public import door."""
        edges: List[Dict[str, Any]] = []
        topic_nodes: Dict[str, Dict[str, Any]] = {}
        for note in notes:
            from_id = node_ids.get(note.relative_path)
            if from_id is None:
                continue
            for target in resolved.get(note.relative_path, []):
                to_id = node_ids.get(target)
                if to_id is None:
                    continue
                edges.append(self._edge_row(from_id, to_id, LINK_RELATION, {
                    "source": SOURCE_TYPE,
                    "vault": vault,
                    "relation": "wikilink",
                    "from_note": note.relative_path,
                    "to_note": target,
                }))
            for tag in note.tags:
                topic_id = _scoped_slug_id("topic", tag, workspace_id)
                topic_nodes.setdefault(topic_id, self._topic_row(
                    topic_id, tag, vault=vault, owner=owner, workspace_id=workspace_id,
                ))
                edges.append(self._edge_row(from_id, topic_id, TAG_RELATION, {
                    "source": SOURCE_TYPE,
                    "vault": vault,
                    "relation": "frontmatter_tag",
                    "tag": tag,
                }))
        reference_edges = sum(1 for edge in edges if edge["type"] == LINK_RELATION)
        tag_edges = len(edges) - reference_edges
        if not edges:
            return
        if self._kg is None:
            summary["edges"] = {
                "status": "skipped",
                "references": 0,
                "tags": 0,
                "detail": "no Knowledge Graph store is bound; notes were ingested without link edges",
            }
            return
        try:
            outcome = self._kg.import_graph_data(
                {
                    "nodes": list(topic_nodes.values()),
                    "edges": edges,
                    "chunks": [],
                    "knowledge_sources": [],
                    "provenance": [],
                },
                mode="merge",
            )
        except Exception as exc:  # noqa: BLE001 — notes already landed; report, never crash
            summary["edges"] = {
                "status": "failed",
                "references": 0,
                "tags": 0,
                "detail": f"link edges could not be written: {exc}",
            }
            return
        summary["links"]["written"] = reference_edges
        summary["edges"] = {
            "status": "written",
            "references": reference_edges,
            "tags": tag_edges,
            "topics": len(topic_nodes),
            "detail": None,
            "index": outcome.get("index"),
        }

    # Row shapes are shared with every other bridge (v11.2.0): two copies of
    # "what an edge row looks like" is two places to forget an encoding.
    _edge_row = staticmethod(edge_row)

    @staticmethod
    def _topic_row(
        topic_id: str,
        tag: str,
        *,
        vault: str,
        owner: Optional[str],
        workspace_id: Optional[str],
    ) -> Dict[str, Any]:
        return topic_row(
            topic_id,
            tag,
            summary=f"Obsidian tag #{tag}",
            metadata={
                "topic": tag,
                "source": SOURCE_TYPE,
                "vault": vault,
                "owner": owner,
                "workspace_id": workspace_id,
            },
        )


__all__ = [
    "DEFAULT_MAX_FILES",
    "DEFAULT_MAX_FILE_BYTES",
    "LINK_RELATION",
    "NOTE_EXTENSIONS",
    "SOURCE_TYPE",
    "TAG_RELATION",
    "ObsidianVaultBridge",
    "VaultNote",
    "extract_link_targets",
    "frontmatter_tags",
    "parse_frontmatter",
]
