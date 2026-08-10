"""Agent-native workspace reorganization — proposals, never a silent tidy-up.

"이 프로젝트를 정리해줘" is a request an agent can now answer without being
trusted with the filesystem. The Brain looks at a folder, asks the graph what
each file is *about*, and stages one review proposal describing every move it
would make. Nothing on disk changes until a person approves it, and approving
applies exactly the moves that were reviewed.

Three rules make this safe enough to hand to a long-running session:

* **No deletions, ever.** The planner emits moves only. There is no code path
  here that removes a file, so the worst outcome of a bad proposal is a file
  in a folder you did not want (recoverable) rather than a file that is gone
  (not). Plan §10's conservative mitigation, made structural.
* **Only what the Brain actually knows.** A file is moved because the graph
  links it to a topic — never because of a guess from its extension. Files the
  Brain has nothing to say about are reported as ``unplaced`` with a reason
  instead of being swept somewhere plausible.
* **Deterministic.** The same folder and the same graph produce the same plan,
  in the same order: candidates are sorted, topics are chosen by
  (weight, title), and collisions resolve to the first path alphabetically.

Every path in and out of this module is workspace-relative and resolved
through the caller's sandboxed ``resolve_path`` (the agent-root resolver), so
a proposal can never name a target outside the workspace.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from latticeai.core.timeutil import now_iso as _now

LOGGER = logging.getLogger(__name__)

#: Review-queue kind for a reorganization proposal. Source stays
#: ``change_proposal`` so the Review Center's approve path applies it through
#: :class:`~latticeai.services.change_proposals.ChangeProposalService`.
REORG_KIND = "folder_reorganization"

#: Folder the plan groups into. One level, named after the topic, so the result
#: is legible without a map.
TOPIC_ROOT = "topics"

#: Node types that stand for a file on disk, and the ones that stand for what a
#: file is about.
FILE_NODE_TYPES = frozenset(
    {
        "File", "CodeFile", "Document", "Image", "Spreadsheet", "SlideDeck",
        "ImageText", "Audio",
    }
)
TOPIC_NODE_TYPES = frozenset({"Topic", "Concept", "Project", "Feature"})

DEFAULT_MAX_MOVES = 20
DEFAULT_SCAN_LIMIT = 400
DEFAULT_GRAPH_LIMIT = 800

_SLUG_RE = re.compile(r"[^0-9A-Za-z가-힣._-]+")


def _slug(value: str) -> str:
    """Folder-safe topic name (Korean kept — the user reads these folders)."""
    return _SLUG_RE.sub("-", str(value or "").strip()).strip("-.").lower()[:48]


def _rel(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _scan(base: Path, limit: int) -> List[Path]:
    """Visible files under ``base``, deepest-stable order, capped at ``limit``."""
    found: List[Path] = []
    for path in sorted(base.rglob("*")):
        if len(found) >= limit:
            break
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        found.append(path)
    return found


def _file_node_index(graph: Any, limit: int) -> Tuple[Dict[str, str], str]:
    """``{file name → graph node id}`` plus the basis for the plan.

    A missing or unreadable graph yields an empty index and a basis that says
    so, which is what makes an empty plan honest rather than mysterious.
    """
    if graph is None:
        return {}, "no_graph"
    try:
        data = graph.graph(limit)
    except Exception:  # noqa: BLE001 — a broken read proposes nothing
        LOGGER.exception("workspace reorganization graph read failed")
        return {}, "graph_unavailable"
    index: Dict[str, str] = {}
    for node in data.get("nodes") or []:
        if str(node.get("type")) not in FILE_NODE_TYPES:
            continue
        for key in _file_keys(node):
            index.setdefault(key, str(node.get("id")))
    return index, "graph"


def _topic_for(graph: Any, node_id: str) -> str:
    """The topic a file node belongs to, or ``""`` when the Brain has none.

    One hop, strongest edge first; ties break on the topic title so the same
    graph always produces the same folder.
    """
    try:
        data = graph.neighbors(node_id)
    except Exception:  # noqa: BLE001 — one unreadable node is not a failure
        LOGGER.exception("workspace reorganization neighbour read failed")
        return ""
    by_id = {str(node.get("id")): node for node in data.get("neighbors") or []}
    best: Optional[Tuple[float, str]] = None
    for edge in data.get("edges") or []:
        far = edge.get("to") if str(edge.get("from")) == node_id else edge.get("from")
        node = by_id.get(str(far))
        if node is None or str(node.get("type")) not in TOPIC_NODE_TYPES:
            continue
        title = str(node.get("title") or "").strip()
        if not title:
            continue
        candidate = (-float(edge.get("weight") or 1.0), title)
        if best is None or candidate < best:
            best = candidate
    return best[1] if best is not None else ""


def _file_keys(node: Dict[str, Any]) -> List[str]:
    """Every name a graph file node might be matched by (lowercased)."""
    metadata = node.get("metadata") or {}
    candidates = [
        metadata.get("relative_path"),
        metadata.get("filename"),
        metadata.get("path"),
        node.get("title"),
    ]
    keys: List[str] = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        for key in (text.lower(), Path(text).name.lower()):
            if key and key not in keys:
                keys.append(key)
    return keys


def plan_reorganization(
    *,
    root: str = "",
    resolve_path: Callable[[str], Path],
    graph: Any = None,
    max_moves: int = DEFAULT_MAX_MOVES,
    scan_limit: int = DEFAULT_SCAN_LIMIT,
    graph_limit: int = DEFAULT_GRAPH_LIMIT,
) -> Dict[str, Any]:
    """What the Brain would move, and what it deliberately would not.

    Read-only: this touches no file and creates no proposal.
    """
    base = resolve_path(root)
    if not base.is_dir():
        return _empty_plan(root, "folder_missing")
    index, basis = _file_node_index(graph, graph_limit)
    moves: List[Dict[str, Any]] = []
    unplaced: List[Dict[str, Any]] = []
    claimed: set = set()
    for path in _scan(base, scan_limit):
        source = _rel(path, base)
        node_id = index.get(source.lower()) or index.get(path.name.lower())
        topic = _topic_for(graph, node_id) if node_id else ""
        if not topic:
            unplaced.append({"path": source, "reason": "brain_has_no_topic"})
            continue
        target = f"{TOPIC_ROOT}/{_slug(topic)}/{path.name}"
        if target == source:
            unplaced.append({"path": source, "reason": "already_in_place"})
            continue
        if target in claimed or (base / target).exists():
            unplaced.append({"path": source, "reason": "target_taken"})
            continue
        claimed.add(target)
        moves.append({
            "source": source,
            "target": target,
            "topic": topic,
            "reason": f"'{topic}' 주제와 이어져 있습니다",
        })
    trimmed = len(moves) > max_moves
    return {
        "available": True,
        "root": root,
        "basis": basis,
        "moves": moves[: max(0, int(max_moves))],
        "move_count": len(moves[: max(0, int(max_moves))]),
        "truncated": trimmed,
        "unplaced": unplaced,
        "unplaced_count": len(unplaced),
        # Structural, not a policy toggle: this planner has no delete path.
        "deletes": [],
        "generated_at": _now(),
    }


def _empty_plan(root: str, reason: str) -> Dict[str, Any]:
    return {
        "available": False,
        "root": root,
        "basis": reason,
        "moves": [],
        "move_count": 0,
        "truncated": False,
        "unplaced": [],
        "unplaced_count": 0,
        "deletes": [],
        "generated_at": _now(),
    }


def propose_reorganization(
    *,
    root: str = "",
    resolve_path: Callable[[str], Path],
    review_queue: Any,
    graph: Any = None,
    user_email: Optional[str] = None,
    workspace_id: Optional[str] = None,
    max_moves: int = DEFAULT_MAX_MOVES,
    audit: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """Stage one review proposal describing the whole reorganization.

    Returns ``{"proposed": None, ...}`` when there is nothing to propose — an
    empty proposal is noise, and a Brain that knows nothing about a folder
    should say so rather than invent a structure.
    """
    plan = plan_reorganization(
        root=root, resolve_path=resolve_path, graph=graph, max_moves=max_moves
    )
    if not plan["moves"]:
        return {"proposed": None, "plan": plan, "reason": plan["basis"]}
    preview = ", ".join(move["source"] for move in plan["moves"][:3])
    item = review_queue.create(
        title=f"폴더 정리 제안: {root or '작업 폴더'} 파일 {plan['move_count']}개",
        summary=(
            f"{preview} 등 {plan['move_count']}개 파일을 주제별 폴더로 옮기는 제안입니다. "
            "삭제는 제안하지 않으며, 승인하기 전에는 아무것도 움직이지 않습니다."
        ),
        source="change_proposal",
        kind=REORG_KIND,
        payload={
            "root": root,
            "moves": plan["moves"],
            "unplaced": plan["unplaced"],
            "basis": plan["basis"],
            "contract": {"moves": "proposal", "deletions": "never_proposed"},
        },
        provenance={
            "proposed_by": "agent",
            "reason": "workspace reorganization",
            "source_detail": "brain topic grouping",
        },
        user_email=user_email,
        workspace_id=workspace_id,
    )
    if audit is not None:
        audit(
            "workspace_reorg_proposed",
            user_email=user_email,
            proposal_id=item.get("id"),
            moves=plan["move_count"],
        )
    return {"proposed": item, "plan": plan, "reason": ""}


def apply_reorganization(
    payload: Dict[str, Any], *, resolve_path: Callable[[str], Path]
) -> Dict[str, Any]:
    """Carry out the reviewed moves. Nothing is ever deleted or overwritten.

    Each move is checked again at apply time: a source that vanished and a
    target that appeared are both reported as skipped rather than forced, so a
    stale proposal degrades into an honest partial result.
    """
    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for move in payload.get("moves") or []:
        source_rel = str(move.get("source") or "")
        target_rel = str(move.get("target") or "")
        if not source_rel or not target_rel:
            skipped.append({"source": source_rel, "reason": "incomplete_move"})
            continue
        source = resolve_path(_join(payload, source_rel))
        target = resolve_path(_join(payload, target_rel))
        if not source.is_file():
            skipped.append({"source": source_rel, "reason": "source_missing"})
            continue
        if target.exists():
            skipped.append({"source": source_rel, "reason": "target_exists"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        applied.append({"source": source_rel, "target": target_rel})
    return {
        "applied": applied,
        "applied_count": len(applied),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "deleted": 0,
    }


def _join(payload: Dict[str, Any], relative: str) -> str:
    root = str(payload.get("root") or "").strip("/")
    return f"{root}/{relative}" if root else relative


__all__ = [
    "DEFAULT_MAX_MOVES",
    "REORG_KIND",
    "TOPIC_ROOT",
    "apply_reorganization",
    "plan_reorganization",
    "propose_reorganization",
]
