"""Personal ontology — the Brain's model of *its owner* (v11.1.0).

Everything else in the graph is knowledge the user collected. This subgraph is
knowledge about the user: what they prefer, what they decided, what they do
every day, who they work with. It is small, it is read on almost every prompt,
and it is the one part of the Brain where being wrong is not a retrieval miss
but an insult — so it is governed differently from the rest:

* **Extraction never writes.** :func:`propose_self_model` reads text and raises
  one review proposal per candidate fact through the same
  ``ReviewQueueService`` door synthesis uses (:class:`ProposalDesk`, which also
  suppresses a subject already waiting for a decision).
  :func:`apply_self_model_proposal` is the only path from a proposal to a node,
  and it writes nothing until ``review_queue.approve`` has returned.
* **The user writes directly.** :func:`upsert_self_model_fact` and
  :func:`delete_self_model_fact` are user-initiated edits — ownership means the
  person can add, correct, and remove their own profile without asking a
  review queue for permission.
* **Deterministic by default.** The extractor is a table of regexes over
  first-person phrasings (Korean and English); the same text always yields the
  same candidates in the same order. An optional ``refiner`` callable may
  improve the *wording* of a candidate — it can never add, drop, or reclassify
  one, and a refiner that raises is ignored.

Shape of the subgraph: one ``Self`` root (``self:root``) with every fact node
(``self:<kind>:<digest>``) pointing at it through a ``PART_OF`` edge. The id
prefix is the membership test, so a fact can never be confused with an ordinary
memory that happens to be typed ``Decision``.

Brain Core isolation: ``review_queue`` and ``store`` are duck-typed; this
module never imports ``latticeai``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .context import approx_tokens
from .synthesis import ProposalDesk
from .utils import now_iso

logger = logging.getLogger(__name__)

#: Review-queue kind for a Self-Model candidate. The source stays the existing
#: ``kg_change_digest`` (see :mod:`lattice_brain.synthesis`) — a new fact about
#: the user is a knowledge change, and the Review Center already renders it.
SELF_MODEL_KIND = "self_model_fact"

#: Node id prefix. Membership in the Self-Model is a fact about identity, not a
#: heuristic over node types, so it is encoded in the id.
SELF_ID_PREFIX = "self:"
SELF_ROOT_ID = "self:root"

#: Fact kind → the graph node type it becomes. ``DECISION`` is the existing
#: node type, deliberately reused (see ``graph/schema.py``).
FACT_NODE_TYPES: Dict[str, str] = {
    "preference": "Preference",
    "decision": "Decision",
    "habit": "Habit",
    "relationship": "Relationship",
    "trait": "Self",
}

#: Rendering order for the summary — who the person is, then what they like,
#: then what they repeat, then what they settled, then who is around them.
KIND_ORDER: Tuple[str, ...] = (
    "trait",
    "preference",
    "habit",
    "decision",
    "relationship",
)

#: Plain-language labels used in the injected summary (Korean product voice).
KIND_LABELS: Dict[str, str] = {
    "trait": "나",
    "preference": "선호",
    "habit": "습관",
    "decision": "결정",
    "relationship": "관계",
}

#: Default token ceiling for :func:`self_model_summary`. Small on purpose: the
#: Self-Model rides along on every prompt, so it must never crowd out the
#: knowledge the question is actually about.
DEFAULT_SUMMARY_TOKENS = 200

_MAX_FACT_CHARS = 120
_WHITESPACE = re.compile(r"\s+")
_TRAILING = " .。!?！？,、;:\n\t\"'“”‘’()[]{}"

# Deterministic extraction table: (kind, pattern, capture group, confidence,
# signal). Order fixes the order candidates are produced in.
_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]", int, float, str], ...] = (
    (
        "decision",
        re.compile(r"결정\s*[:：]\s*(?P<v>[^\n]+)"),
        1,
        0.9,
        "ko_decision_marker",
    ),
    (
        "decision",
        re.compile(r"(?P<v>[^\n.。]+?)\s*하기로\s*(?:했|결정했)"),
        1,
        0.75,
        "ko_decided_to",
    ),
    (
        "decision",
        re.compile(r"\bDecision\s*:\s*(?P<v>[^\n]+)", re.IGNORECASE),
        1,
        0.9,
        "en_decision_marker",
    ),
    (
        "decision",
        re.compile(r"\b(?:I|we)\s+decided\s+to\s+(?P<v>[^\n.!?]+)", re.IGNORECASE),
        1,
        0.75,
        "en_decided_to",
    ),
    (
        "preference",
        re.compile(
            r"(?:나는|저는|내가|제가)\s*(?P<v>[^\n.。]+?)\s*(?:을|를|이|가)?"
            r"\s*(?:좋아|선호|싫어)(?:합니다|한다|해요|해|하고)"
        ),
        1,
        0.7,
        "ko_first_person_preference",
    ),
    (
        "preference",
        re.compile(
            r"\bI\s+(?:prefer|like|love|hate|dislike|avoid)\s+(?P<v>[^\n.!?]+)",
            re.IGNORECASE,
        ),
        1,
        0.7,
        "en_first_person_preference",
    ),
    (
        # The frequency word stays inside the capture: "회고를 씁니다" is a
        # sentence, "매일 회고를 씁니다" is a habit.
        "habit",
        re.compile(r"(?P<v>(?:매일|매주|매달|아침마다|항상|늘)\s*[^\n.。!?]+)"),
        1,
        0.65,
        "ko_routine",
    ),
    (
        "habit",
        re.compile(
            r"\bI\s+(?P<v>(?:always|usually|every\s+(?:morning|day|week))"
            r"\s+[^\n.!?]+)",
            re.IGNORECASE,
        ),
        1,
        0.65,
        "en_routine",
    ),
    (
        "relationship",
        re.compile(
            r"(?:내|제)\s*(?P<v>(?:동료|팀장|매니저|친구|파트너|상사|멘토)\s+\S+)"
        ),
        1,
        0.6,
        "ko_relationship",
    ),
    (
        "relationship",
        re.compile(
            r"\bmy\s+(?P<v>(?:colleague|manager|teammate|friend|partner|mentor|boss)"
            r"\s+[^\n.!?,]+)",
            re.IGNORECASE,
        ),
        1,
        0.6,
        "en_relationship",
    ),
    (
        "trait",
        re.compile(
            r"(?:나는|저는)\s*(?P<v>[^\n.。]*?(?:개발자|디자이너|엔지니어|연구원|학생|기획자))"
            r"\s*(?:입니다|이다|다|예요)"
        ),
        1,
        0.6,
        "ko_role",
    ),
    (
        "trait",
        re.compile(r"\bI\s+am\s+an?\s+(?P<v>[^\n.!?]+)", re.IGNORECASE),
        1,
        0.6,
        "en_role",
    ),
)


class SelfModelError(ValueError):
    """Raised when a Self-Model operation cannot be carried out as asked.

    Carries a machine-readable ``code`` alongside the developer message: Brain
    Core cannot reach the server's message catalog (isolation), so the API
    layer translates the code instead of forwarding an English sentence to a
    Korean user.
    """

    def __init__(self, message: str, *, code: str = "invalid") -> None:
        self.code = code
        super().__init__(message)


# ── extraction (deterministic) ───────────────────────────────────────────────


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", str(text or "")).strip().strip(_TRAILING)[
        :_MAX_FACT_CHARS
    ]


def fact_id(kind: str, text: str) -> str:
    """Stable id for a fact — the same statement always lands on the same node."""
    digest = hashlib.sha256(f"{kind}|{text.lower()}".encode()).hexdigest()[:12]
    return f"{SELF_ID_PREFIX}{kind}:{digest}"


def extract_self_model(
    text: str,
    *,
    source: Optional[str] = None,
    refiner: Optional[Callable[[str], str]] = None,
) -> List[Dict[str, Any]]:
    """Candidate Self-Model facts found in ``text``.

    Pure and deterministic: a table of first-person patterns, deduplicated on
    (kind, lowercased text), ordered by (kind, text). ``refiner`` — a model,
    when one is available — may only rewrite the wording of a candidate the
    regexes already found; anything it returns that is empty or too long is
    ignored, as is a refiner that raises.
    """
    found: Dict[str, Dict[str, Any]] = {}
    for kind, pattern, group, confidence, signal in _PATTERNS:
        for match in pattern.finditer(str(text or "")):
            value = _normalize(match.group(group))
            if len(value) < 2:
                continue
            value = _refine(value, refiner)
            identifier = fact_id(kind, value)
            if identifier in found:
                continue
            found[identifier] = {
                "id": identifier,
                "kind": kind,
                "text": value,
                "confidence": confidence,
                "signal": signal,
                "source": source or "",
            }
    return sorted(found.values(), key=lambda fact: (fact["kind"], fact["text"]))


def _refine(value: str, refiner: Optional[Callable[[str], str]]) -> str:
    """Optional model pass over one candidate's wording (never its meaning)."""
    if refiner is None:
        return value
    try:
        written = _normalize(refiner(value))
    except Exception:  # noqa: BLE001 — a model may never break extraction
        logger.exception("self-model refiner failed")
        return value
    return written or value


# ── proposals (the only agent/background write path) ─────────────────────────


def propose_self_model(
    store: Any,
    review_queue: Any,
    *,
    text: str = "",
    texts: Optional[Sequence[str]] = None,
    source: Optional[str] = None,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
    refiner: Optional[Callable[[str], str]] = None,
    max_proposals: int = 5,
) -> Dict[str, Any]:
    """Raise one review proposal per newly-noticed fact about the user.

    Facts already in the subgraph are skipped (nothing is proposed twice), and
    :class:`ProposalDesk` suppresses subjects already waiting for a decision.
    Nothing is written to the graph here.
    """
    corpus = [text, *(texts or [])]
    candidates: List[Dict[str, Any]] = []
    seen: set = set()
    for chunk in corpus:
        for fact in extract_self_model(chunk, source=source, refiner=refiner):
            if fact["id"] in seen:
                continue
            seen.add(fact["id"])
            candidates.append(fact)
    known = {row["id"] for row in _read_facts(store, workspace_id=workspace_id)}
    fresh = [fact for fact in candidates if fact["id"] not in known]
    desk = ProposalDesk(review_queue, user_email=user_email, workspace_id=workspace_id)
    open_keys = desk.open_keys()
    proposed: List[Dict[str, Any]] = []
    for fact in fresh[: max(0, int(max_proposals))]:
        item = desk.propose(
            kind=SELF_MODEL_KIND,
            key=fact["id"],
            title=f"나에 대한 새 사실: {fact['text']}",
            summary=(
                f"대화에서 '{fact['text']}'를 읽었습니다. "
                f"내 프로필({KIND_LABELS[fact['kind']]})에 추가할까요? "
                "승인하기 전에는 저장되지 않습니다."
            ),
            open_keys=open_keys,
            payload={"fact": fact, "node_type": FACT_NODE_TYPES[fact["kind"]]},
        )
        if item is not None:
            proposed.append(item)
    return {
        "available": True,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "already_known": len(candidates) - len(fresh),
        "proposed": proposed,
        "proposed_count": len(proposed),
        "suppressed": desk.suppressed,
        "generated_at": now_iso(),
    }


def apply_self_model_proposal(
    store: Any,
    review_queue: Any,
    item_id: str,
    *,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Approve a Self-Model proposal and write the fact into the subgraph.

    The single door from a proposal to a node, gated twice the way
    ``resolve_contradiction`` is: the item must be a Self-Model proposal, and
    ``review_queue.approve`` must return before anything is written.
    """
    item = review_queue.get(item_id, workspace_id=workspace_id)
    if str(item.get("kind")) != SELF_MODEL_KIND:
        raise SelfModelError(
            f"review item {item_id} is not a Self-Model proposal",
            code="not_a_proposal",
        )
    fact = dict((item.get("payload") or {}).get("fact") or {})
    kind = str(fact.get("kind") or "")
    text = _normalize(str(fact.get("text") or ""))
    if kind not in FACT_NODE_TYPES or not text:
        raise SelfModelError(
            f"review item {item_id} carries no Self-Model fact",
            code="empty_proposal",
        )
    approved = review_queue.approve(item_id, workspace_id=workspace_id)
    node = _write_fact(
        store,
        kind=kind,
        text=text,
        workspace_id=workspace_id or item.get("workspace_id"),
        origin="proposal",
        confidence=float(fact.get("confidence") or 0.0),
        signal=str(fact.get("signal") or ""),
        item_id=item_id,
    )
    return {
        "item_id": item_id,
        "status": str(approved.get("status") or ""),
        "fact": node,
        "applied_at": now_iso(),
    }


# ── user-initiated edits (direct writes — ownership) ─────────────────────────


def upsert_self_model_fact(
    store: Any,
    *,
    kind: str,
    text: str,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Add or correct one fact directly. The user owns their own profile."""
    kind = str(kind or "").strip().lower()
    value = _normalize(text)
    if kind not in FACT_NODE_TYPES:
        raise SelfModelError(
            f"kind must be one of {sorted(FACT_NODE_TYPES)}", code="invalid_kind"
        )
    if not value:
        raise SelfModelError("a Self-Model fact needs text", code="text_required")
    return _write_fact(
        store,
        kind=kind,
        text=value,
        workspace_id=workspace_id,
        origin="user",
        confidence=1.0,
        signal="user_edit",
        item_id=None,
    )


def delete_self_model_fact(store: Any, node_id: str) -> Dict[str, Any]:
    """Remove one fact (or the whole root) from the subgraph, permanently."""
    identifier = str(node_id or "").strip()
    if not identifier.startswith(SELF_ID_PREFIX):
        raise SelfModelError(f"not a Self-Model node: {identifier}", code="not_self_model")
    with store._connect() as conn:
        deleted = conn.execute(
            "DELETE FROM nodes WHERE id=?", (identifier,)
        ).rowcount
        conn.execute("DELETE FROM nodes_v2 WHERE id=?", (identifier,))
    if not deleted:
        raise SelfModelError(f"Self-Model node not found: {identifier}", code="not_found")
    return {"status": "ok", "id": identifier, "deleted_at": now_iso()}


# ── reads ────────────────────────────────────────────────────────────────────


def list_self_model(
    store: Any,
    *,
    workspace_id: Optional[str] = None,
    allowed_workspaces: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Every fact the Brain holds about its owner, grouped for display."""
    facts = _read_facts(
        store, workspace_id=workspace_id, allowed_workspaces=allowed_workspaces
    )
    counts: Dict[str, int] = {kind: 0 for kind in KIND_ORDER}
    for fact in facts:
        counts[fact["kind"]] += 1
    return {
        "available": True,
        "facts": facts,
        "count": len(facts),
        "counts": counts,
        "kinds": list(KIND_ORDER),
        "generated_at": now_iso(),
    }


def self_model_summary(
    store: Any,
    *,
    limit_tokens: int = DEFAULT_SUMMARY_TOKENS,
    workspace_id: Optional[str] = None,
    allowed_workspaces: Optional[Iterable[str]] = None,
) -> str:
    """Plain-text Self-Model summary for injection into a model's context.

    Deterministic and bounded: facts are rendered in :data:`KIND_ORDER`, and
    lines are added only while the whole block stays inside ``limit_tokens``.
    An empty subgraph returns ``""`` — the caller injects nothing rather than a
    header with nothing under it.
    """
    facts = _read_facts(
        store, workspace_id=workspace_id, allowed_workspaces=allowed_workspaces
    )
    if not facts or limit_tokens <= 0:
        return ""
    header = "사용자에 대해 확인된 사실:"
    lines: List[str] = []
    for kind in KIND_ORDER:
        for fact in facts:
            if fact["kind"] != kind:
                continue
            line = f"- {KIND_LABELS[kind]}: {fact['text']}"
            if approx_tokens("\n".join([header, *lines, line])) > limit_tokens:
                return "\n".join([header, *lines]) if lines else ""
            lines.append(line)
    return "\n".join([header, *lines])


# ── graph plumbing ───────────────────────────────────────────────────────────


def _write_fact(
    store: Any,
    *,
    kind: str,
    text: str,
    workspace_id: Optional[str],
    origin: str,
    confidence: float,
    signal: str,
    item_id: Optional[str],
) -> Dict[str, Any]:
    """Write the root (if needed), the fact node, and the ``PART_OF`` edge."""
    node_id = fact_id(kind, text)
    metadata = {
        "self_model": True,
        "self_model_kind": kind,
        "origin": origin,
        "confidence": confidence,
        "signal": signal,
        "workspace_id": workspace_id,
        "review_item_id": item_id,
    }
    with store._connect() as conn:
        store._upsert_node(
            conn,
            SELF_ROOT_ID,
            "Self",
            "나",
            "Brain이 사용자에 대해 알고 있는 사실의 뿌리입니다.",
            metadata={"self_model": True, "self_model_kind": "root"},
            workspace_id=workspace_id,
        )
        store._upsert_node(
            conn,
            node_id,
            FACT_NODE_TYPES[kind],
            text,
            text,
            metadata=metadata,
            workspace_id=workspace_id,
        )
        store._upsert_edge(conn, node_id, SELF_ROOT_ID, "PART_OF")
    return {
        "id": node_id,
        "kind": kind,
        "type": FACT_NODE_TYPES[kind],
        "text": text,
        "origin": origin,
        "confidence": confidence,
        "signal": signal,
        "workspace_id": workspace_id,
    }


def _allowed_set(
    workspace_id: Optional[str], allowed_workspaces: Optional[Iterable[str]]
) -> Optional[set]:
    """Normalize the two scoping spellings into one allow-set (``None`` = all).

    ``allowed_workspaces`` is the scoping vocabulary the graph reads already
    use (``filter_scoped_nodes``); ``workspace_id`` is the single-scope sugar
    the API surfaces speak. A fact with no workspace is personal-global and
    stays visible in both.
    """
    if allowed_workspaces is not None:
        return {str(item) for item in allowed_workspaces if item}
    if workspace_id is None:
        return None
    return {str(workspace_id)}


def _read_facts(
    store: Any,
    *,
    workspace_id: Optional[str] = None,
    allowed_workspaces: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Fact rows for a workspace, ordered deterministically (kind, then text).

    Reads the legacy ``nodes`` table, which the v4 write door maintains as the
    compatibility projection of ``nodes_v2`` — so this answers the same rows in
    both read modes. A store that cannot be read yields no facts rather than
    raising into a prompt-assembly path.
    """
    if not hasattr(store, "_connect"):
        # Not a Brain store (a doc-generation seam may be handed a stand-in).
        # There is no profile to read, and that is not an error worth logging.
        return []
    try:
        with store._connect() as conn:
            rows = conn.execute(
                "SELECT id, type, title, summary, metadata_json, updated_at "
                "FROM nodes WHERE id LIKE ? AND id != ? ORDER BY id ASC",
                (f"{SELF_ID_PREFIX}%", SELF_ROOT_ID),
            ).fetchall()
    except Exception:  # noqa: BLE001 — an unreadable Brain injects nothing
        logger.exception("self-model read failed")
        return []
    facts = [fact for fact in (_row_to_fact(row) for row in rows) if fact]
    allowed = _allowed_set(workspace_id, allowed_workspaces)
    if allowed is not None:
        facts = [
            fact
            for fact in facts
            if not fact["workspace_id"] or str(fact["workspace_id"]) in allowed
        ]
    return sorted(facts, key=lambda fact: (KIND_ORDER.index(fact["kind"]), fact["text"]))


def _row_to_fact(row: Any) -> Optional[Dict[str, Any]]:
    metadata = _loads(row["metadata_json"])
    kind = str(metadata.get("self_model_kind") or "")
    if kind not in FACT_NODE_TYPES:
        return None
    return {
        "id": row["id"],
        "kind": kind,
        "type": row["type"],
        "text": row["title"],
        "origin": str(metadata.get("origin") or ""),
        "confidence": metadata.get("confidence"),
        "signal": str(metadata.get("signal") or ""),
        "workspace_id": metadata.get("workspace_id"),
        "updated_at": row["updated_at"],
    }


def _loads(raw: Any) -> Dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def summary_for_prompt(
    store: Any,
    *,
    limit_tokens: int = DEFAULT_SUMMARY_TOKENS,
    workspace_id: Optional[str] = None,
    allowed_workspaces: Optional[Iterable[str]] = None,
) -> str:
    """:func:`self_model_summary` that never raises — the injection seam.

    Prompt assembly must not fail because a profile could not be read; an
    unreadable Self-Model injects nothing, exactly like an empty one.
    """
    try:
        return self_model_summary(
            store,
            limit_tokens=limit_tokens,
            workspace_id=workspace_id,
            allowed_workspaces=allowed_workspaces,
        )
    except Exception:  # noqa: BLE001 — see docstring
        logger.exception("self-model summary failed")
        return ""


__all__ = [
    "DEFAULT_SUMMARY_TOKENS",
    "FACT_NODE_TYPES",
    "KIND_LABELS",
    "KIND_ORDER",
    "SELF_ID_PREFIX",
    "SELF_MODEL_KIND",
    "SELF_ROOT_ID",
    "SelfModelError",
    "apply_self_model_proposal",
    "delete_self_model_fact",
    "extract_self_model",
    "fact_id",
    "list_self_model",
    "propose_self_model",
    "self_model_summary",
    "summary_for_prompt",
    "upsert_self_model_fact",
]
