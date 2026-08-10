"""Background Brain synthesis — every output is a proposal (v11.1.0).

The Brain stops being a filing cabinet you query and starts noticing things:
that two memories disagree, that a topic keeps recurring without ever being
named, that two notes always show up together but were never linked, that a
pile of chat fragments has decayed into noise.

**Nothing here writes knowledge.** Every observation leaves this module as a
review proposal (``ReviewQueueService.create``) and is applied only when a
person approves it — :func:`resolve_contradiction` is the single door through
which a synthesis conclusion ever reaches the graph, and it opens only after
``approve()`` has returned. Tests assert that directly.

Design contracts
----------------
* **Deterministic.** Token overlap, degree, and clock arithmetic only — the
  same graph yields the same proposals. A ``summarizer`` callable may be
  injected to make the *wording* of the weekly brief nicer; it can never
  change what is proposed, and a summarizer that raises is ignored.
* **Event-driven.** :class:`SynthesisTrigger` counts successful ingests and
  fires at ``LATTICEAI_SYNTHESIS_THRESHOLD`` (default 25) new nodes. It holds
  a counter and nothing else, so a caller can drive it from an ingestion
  pipeline, a scheduler, or a unit test without a server.
* **Brain Core isolation.** ``review_queue`` is duck-typed
  (``create``/``list``/``get``/``approve``); this module never imports
  ``latticeai``.
* **Idempotent proposals.** A subject already sitting open in the review queue
  is not proposed twice — the inbox is a place to decide, not a firehose.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .gates import FeatureGate
from .graph.proactive import ProactiveBrain
from .graph.schema import KGStoreV2
from .quality import content_signature
from .utils import now_iso

logger = logging.getLogger(__name__)

#: Review-queue source for everything this module raises. It is an existing
#: source (the Review Center already renders it) — synthesis is a knowledge
#: change digest, so it does not need a surface of its own.
SYNTHESIS_REVIEW_SOURCE = "kg_change_digest"

CONTRADICTION_KIND = "contradiction"
CONCEPT_KIND = "concept_cluster"
EDGE_KIND = "missing_edge"
CONSOLIDATION_KIND = "consolidation"

#: How a user may settle a contradiction. ``keep_both_temporal`` is the
#: honest default answer for a Brain: both memories were true, just not at the
#: same time.
CONTRADICTION_RESOLUTIONS = ("keep_old", "replace", "keep_both_temporal")

SYNTHESIS_THRESHOLD_ENV = "LATTICEAI_SYNTHESIS_THRESHOLD"
DEFAULT_SYNTHESIS_THRESHOLD = 25

#: Whether the Brain is allowed to start a pass *by itself*. Default on — this
#: is the behaviour 11.1.0 shipped — and it governs only the automatic path
#: (:meth:`BrainSynthesizer.run_if_due`). An explicit run a person asked for is
#: never gated: turning "tidy up on its own" off means the Brain stops deciding
#: when, not that the button stops working.
SYNTHESIS_ENV = "LATTICEAI_SYNTHESIS"
SYNTHESIS_GATE = FeatureGate(
    SYNTHESIS_ENV,
    default=True,
    name="synthesis",
    detail="The Brain reviews accumulated material on its own and proposes tidy-ups.",
)

_MIN_SHARED_TOKENS = 3
_MIN_CLUSTER_MEMBERS = 3
_EDGE_SIMILARITY = 0.35
_MAX_PROPOSALS_PER_KIND = 5
_COMMON_TOKEN_RATIO = 0.4


def _default_threshold() -> int:
    """Read the ingest threshold from the environment (bad values → default)."""
    raw = os.getenv(SYNTHESIS_THRESHOLD_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SYNTHESIS_THRESHOLD
    return value if value > 0 else DEFAULT_SYNTHESIS_THRESHOLD


def _text_of(node: Dict[str, Any]) -> str:
    title = str(node.get("title") or "").strip()
    summary = str(node.get("summary") or "").strip()
    return f"{title} {summary}".strip()


def _title_of(node: Dict[str, Any]) -> str:
    return str(node.get("title") or node.get("id") or "").strip()


# ── ingest-driven trigger ────────────────────────────────────────────────────


class SynthesisTrigger:
    """Counts successful ingests and reports when a synthesis run is due.

    Deliberately tiny and side-effect free: it owns *when*, never *what*. The
    ingestion pipeline (or any caller with an ingest result) feeds it through
    :meth:`observe_ingest`; the counter resets on every fire, so a long import
    schedules a run per ``threshold`` items rather than one at the end.
    """

    def __init__(self, *, threshold: Optional[int] = None) -> None:
        resolved = _default_threshold() if threshold is None else int(threshold)
        self.threshold = max(1, resolved)
        self._pending = 0
        self._fired = 0
        self._last_fired_at: Optional[str] = None

    def record(self, count: int = 1) -> bool:
        """Add ``count`` new nodes; ``True`` when that reaches the threshold."""
        self._pending += max(0, int(count))
        if self._pending < self.threshold:
            return False
        self._pending = 0
        self._fired += 1
        self._last_fired_at = now_iso()
        return True

    def observe_ingest(self, result: Any) -> bool:
        """Count one ingest result (dataclass or dict); ``True`` when due.

        Only a successful, non-duplicate ingest is new knowledge — a blocked,
        failed, or deduplicated item must not push the Brain toward a run it
        has nothing to say in.
        """
        status = str(_field(result, "status") or "")
        duplicate = bool(_field(result, "duplicate"))
        if status != "ok" or duplicate:
            return False
        return self.record(1)

    def status(self) -> Dict[str, Any]:
        return {
            "threshold": self.threshold,
            "pending": self._pending,
            "runs": self._fired,
            "last_fired_at": self._last_fired_at,
            "due_in": self.threshold - self._pending,
        }


def _field(source: Any, name: str) -> Any:
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


# ── proposal desk ────────────────────────────────────────────────────────────


class ProposalDesk:
    """The only way this module reaches the Brain: the review queue.

    Wraps ``ReviewQueueService`` with one extra rule — a subject that is
    already waiting for a decision is not proposed again. ``proposal_key`` in
    the payload is that subject's identity.
    """

    def __init__(
        self,
        review_queue: Any,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> None:
        self._queue = review_queue
        self._user_email = user_email
        self._workspace_id = workspace_id
        self.suppressed = 0

    def open_keys(self) -> Set[str]:
        """Proposal keys currently awaiting a decision."""
        try:
            listing = self._queue.list(
                workspace_id=self._workspace_id,
                source=SYNTHESIS_REVIEW_SOURCE,
            )
        except Exception:  # noqa: BLE001 — an unreadable inbox must not block
            logger.exception("review queue listing failed")
            return set()
        keys: Set[str] = set()
        for item in listing.get("items") or []:
            if str(item.get("effective_status") or item.get("status")) not in {
                "pending",
                "snoozed",
            }:
                continue
            key = str((item.get("payload") or {}).get("proposal_key") or "")
            if key:
                keys.add(key)
        return keys

    def propose(
        self,
        *,
        kind: str,
        key: str,
        title: str,
        summary: str,
        payload: Dict[str, Any],
        open_keys: Set[str],
    ) -> Optional[Dict[str, Any]]:
        """Create one review item, or ``None`` when the subject is already open."""
        if key in open_keys:
            self.suppressed += 1
            return None
        open_keys.add(key)
        item = self._queue.create(
            title=title,
            summary=summary,
            source=SYNTHESIS_REVIEW_SOURCE,
            kind=kind,
            payload={**payload, "proposal_key": key, "summary_ko": summary},
            provenance={"pipeline": "brain-synthesis", "proposal_key": key},
            user_email=self._user_email,
            workspace_id=self._workspace_id,
        )
        return item


def _pair_key(prefix: str, left: str, right: str) -> str:
    low, high = sorted((str(left), str(right)))
    return f"{prefix}:{low}|{high}"


# ── 2.1 contradiction → proposal ─────────────────────────────────────────────


def propose_contradictions(
    store: Any,
    review_queue: Any,
    *,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
    limit: Optional[int] = None,
    sample: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    brain: Optional[ProactiveBrain] = None,
    max_proposals: int = _MAX_PROPOSALS_PER_KIND,
) -> Dict[str, Any]:
    """Turn detected contradictions into decisions the user can actually make.

    Reads ``ProactiveBrain.detect_contradictions`` and raises one review item
    per contradicting pair, carrying both memories, which one is older, and
    the three ways out (keep the old one / replace it / keep both with time
    ranges). Nothing is written to the graph here.
    """
    detector = brain or ProactiveBrain(store)
    data = sample if sample is not None else detector.sample(
        workspace_id=workspace_id, limit=limit
    )
    found = detector.contradictions_in(data["nodes"], data["edges"])
    by_id = {str(node.get("id")): node for node in data["nodes"]}
    desk = ProposalDesk(review_queue, user_email=user_email, workspace_id=workspace_id)
    open_keys = desk.open_keys()
    proposed: List[Dict[str, Any]] = []
    for pair in found.get("node_pairs") or []:
        if len(proposed) >= max_proposals:
            break
        item = _propose_one_contradiction(desk, pair, by_id, open_keys)
        if item is not None:
            proposed.append(item)
    return {
        "available": True,
        "pairs_detected": len(found.get("node_pairs") or []),
        "proposed": proposed,
        "proposed_count": len(proposed),
        "suppressed": desk.suppressed,
        "nodes_scanned": len(data["nodes"]),
        "generated_at": now_iso(),
    }


def _propose_one_contradiction(
    desk: ProposalDesk,
    pair: Dict[str, Any],
    by_id: Dict[str, Dict[str, Any]],
    open_keys: Set[str],
) -> Optional[Dict[str, Any]]:
    left_id = str(pair.get("left_id") or "")
    right_id = str(pair.get("right_id") or "")
    if not left_id or not right_id or left_id == right_id:
        return None
    older_id, newer_id = _order_by_age(left_id, right_id, by_id)
    older = by_id.get(older_id) or {}
    newer = by_id.get(newer_id) or {}
    older_title = _title_of(older) or older_id
    newer_title = _title_of(newer) or newer_id
    summary = (
        f"'{older_title}'와(과) '{newer_title}'가 서로 어긋납니다. "
        "예전 기억을 그대로 둘지, 새 기억으로 바꿀지, "
        "둘 다 남기고 각각 언제 맞았는지 표시할지 골라주세요."
    )
    return desk.propose(
        kind=CONTRADICTION_KIND,
        key=_pair_key(CONTRADICTION_KIND, older_id, newer_id),
        title=f"모순된 기억: {older_title} ↔ {newer_title}",
        summary=summary,
        open_keys=open_keys,
        payload={
            "older": _memory_brief(older_id, older, pair, "left"),
            "newer": _memory_brief(newer_id, newer, pair, "right"),
            "signal": pair.get("signal"),
            "options": [
                {"id": "keep_old", "label": "예전 기억을 유지"},
                {"id": "replace", "label": "새 기억으로 교체"},
                {"id": "keep_both_temporal", "label": "둘 다 유지하고 기간 표시"},
            ],
        },
    )


def _memory_brief(
    node_id: str, node: Dict[str, Any], pair: Dict[str, Any], side: str
) -> Dict[str, Any]:
    content = pair.get(f"{side}_content")
    if str(pair.get(f"{side}_id") or "") != node_id:
        other = "right" if side == "left" else "left"
        content = pair.get(f"{other}_content")
    return {
        "id": node_id,
        "title": _title_of(node),
        "type": node.get("type"),
        "updated_at": node.get("updated_at"),
        "content": str(content or "")[:200],
    }


def _order_by_age(
    left_id: str, right_id: str, by_id: Dict[str, Dict[str, Any]]
) -> Tuple[str, str]:
    """(older, newer) by ``updated_at``; ties break on id so it is stable."""
    left_ts = str((by_id.get(left_id) or {}).get("updated_at") or "")
    right_ts = str((by_id.get(right_id) or {}).get("updated_at") or "")
    if (left_ts, left_id) <= (right_ts, right_id):
        return left_id, right_id
    return right_id, left_id


class ContradictionResolutionError(ValueError):
    """Raised when a contradiction cannot be resolved as asked."""


def resolve_contradiction(
    store: Any,
    review_queue: Any,
    item_id: str,
    *,
    resolution: str,
    workspace_id: Optional[str] = None,
    at: Optional[str] = None,
) -> Dict[str, Any]:
    """Approve a contradiction proposal and stamp the graph accordingly.

    This is the **only** write path in the module, and it is gated twice: the
    item must be a contradiction proposal, and ``review_queue.approve`` must
    return before a single column is touched. A rejected or unknown resolution
    raises before the approval, so a bad request cannot burn the item.

    * ``replace`` — the older memory stops being true now and points at its
      successor (``valid_to`` + ``superseded_by``).
    * ``keep_old`` — the *newer* memory is the mistake: it is retired and
      points back at the older one.
    * ``keep_both_temporal`` — both stay, with adjoining windows: the older
      one was true until now, the newer one from now on.
    """
    if resolution not in CONTRADICTION_RESOLUTIONS:
        raise ContradictionResolutionError(
            f"resolution must be one of {list(CONTRADICTION_RESOLUTIONS)}"
        )
    item = review_queue.get(item_id, workspace_id=workspace_id)
    if str(item.get("kind")) != CONTRADICTION_KIND:
        raise ContradictionResolutionError(
            f"review item {item_id} is not a contradiction proposal"
        )
    payload = dict(item.get("payload") or {})
    older_id = str((payload.get("older") or {}).get("id") or "")
    newer_id = str((payload.get("newer") or {}).get("id") or "")
    if not older_id or not newer_id:
        raise ContradictionResolutionError(
            f"review item {item_id} carries no memory pair to resolve"
        )

    approved = review_queue.approve(item_id, workspace_id=workspace_id)
    moment = at or now_iso()
    stamps = _stamp_resolution(store, older_id, newer_id, resolution, moment)
    return {
        "item_id": item_id,
        "resolution": resolution,
        "status": str(approved.get("status") or ""),
        "applied_at": moment,
        "stamps": stamps,
    }


def _stamp_resolution(
    store: Any, older_id: str, newer_id: str, resolution: str, moment: str
) -> List[Dict[str, Any]]:
    v2 = KGStoreV2(getattr(store, "db_path", store))
    if resolution == "keep_old":
        plan = [{"node_id": newer_id, "valid_to": moment, "superseded_by": older_id}]
    elif resolution == "replace":
        plan = [
            {"node_id": older_id, "valid_to": moment, "superseded_by": newer_id},
            {"node_id": newer_id, "valid_from": moment},
        ]
    else:  # keep_both_temporal — validated by the caller
        plan = [
            {"node_id": older_id, "valid_to": moment},
            {"node_id": newer_id, "valid_from": moment},
        ]
    applied: List[Dict[str, Any]] = []
    for stamp in plan:
        node_id = stamp["node_id"]
        fields = {key: value for key, value in stamp.items() if key != "node_id"}
        applied.append(
            {
                "node_id": node_id,
                **fields,
                "updated": v2.stamp_node_validity(node_id, **fields),
            }
        )
    return applied


# ── 2.4 importance / decay → consolidation proposals ─────────────────────────


def propose_consolidation(
    store: Any,
    review_queue: Any,
    *,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
    limit: Optional[int] = None,
    sample: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    brain: Optional[ProactiveBrain] = None,
    min_candidates: int = 3,
    max_proposals: int = _MAX_PROPOSALS_PER_KIND,
) -> Dict[str, Any]:
    """Offer to fold the least-used episodic memories into one summary node.

    One proposal per batch, not per fragment: "these 12 chat scraps have not
    been touched in months — shall I fold them into a single summary?" is a
    decision a person can make; twelve of them is a chore.
    """
    detector = brain or ProactiveBrain(store)
    report = detector.importance_report(
        workspace_id=workspace_id, limit=limit, sample=sample
    )
    candidates = list(report.get("candidates") or [])[: max(1, int(max_proposals) * 4)]
    desk = ProposalDesk(review_queue, user_email=user_email, workspace_id=workspace_id)
    proposed: List[Dict[str, Any]] = []
    if len(candidates) >= max(1, int(min_candidates)):
        open_keys = desk.open_keys()
        titles = ", ".join(str(c.get("title") or c.get("id")) for c in candidates[:3])
        item = desk.propose(
            kind=CONSOLIDATION_KIND,
            key="consolidation:" + "|".join(sorted(str(c.get("id")) for c in candidates)),
            title=f"오래된 기록 {len(candidates)}건 정리",
            summary=(
                f"{titles} 등 {len(candidates)}건이 오랫동안 쓰이지 않았습니다. "
                "하나의 요약으로 묶어둘까요? 원본은 그대로 남습니다."
            ),
            open_keys=open_keys,
            payload={
                "candidates": candidates,
                "half_life_days": report.get("half_life_days"),
                "access_source": report.get("access_source"),
            },
        )
        if item is not None:
            proposed.append(item)
    return {
        "available": True,
        "candidate_count": len(candidates),
        "proposed": proposed,
        "proposed_count": len(proposed),
        "suppressed": desk.suppressed,
        "report": report,
        "generated_at": now_iso(),
    }


# ── 2.3 background synthesis job ─────────────────────────────────────────────


class BrainSynthesizer:
    """Event-driven synthesis: contradictions, concepts, links, consolidation.

    ``run()`` takes one graph sample and hands the same rows to every pass, so
    a run is a single consistent view of the Brain. Every output is a review
    proposal; the returned ``brief`` is read-only text for the Brain Brief.
    """

    def __init__(
        self,
        store: Any,
        review_queue: Any,
        *,
        trigger: Optional[SynthesisTrigger] = None,
        summarizer: Optional[Callable[[str], str]] = None,
        sample_limit: Optional[int] = None,
    ) -> None:
        self._store = store
        self._queue = review_queue
        self.trigger = trigger or SynthesisTrigger()
        self._summarizer = summarizer
        self._sample_limit = sample_limit

    # -- trigger seam ----------------------------------------------------
    def observe_ingest(self, result: Any) -> bool:
        """Feed one ingest result to the trigger; ``True`` when a run is due."""
        return self.trigger.observe_ingest(result)

    def run_if_due(
        self, result: Any, *, workspace_id: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run synthesis only when this ingest pushed the counter over.

        The gate is checked *before* the counter moves, so "off" means off: a
        long import while the switch is down does not bank a pass that fires the
        instant someone turns it back on.
        """
        if not SYNTHESIS_GATE.enabled():
            return None
        if not self.observe_ingest(result):
            return None
        return self.run(workspace_id=workspace_id, user_email=user_email)

    # -- the run ---------------------------------------------------------
    def run(
        self, *, workspace_id: Optional[str] = None, user_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        brain = ProactiveBrain(self._store)
        sample = brain.sample(workspace_id=workspace_id, limit=self._sample_limit)
        contradictions = propose_contradictions(
            self._store, self._queue,
            workspace_id=workspace_id, user_email=user_email,
            sample=sample, brain=brain,
        )
        consolidation = propose_consolidation(
            self._store, self._queue,
            workspace_id=workspace_id, user_email=user_email,
            sample=sample, brain=brain,
        )
        concepts = self._propose_concepts(sample, workspace_id, user_email)
        links = self._propose_links(sample, workspace_id, user_email)
        counts = {
            "contradictions": contradictions["proposed_count"],
            "concepts": len(concepts["proposed"]),
            "links": len(links["proposed"]),
            "consolidation": consolidation["proposed_count"],
        }
        return {
            "nodes_scanned": len(sample["nodes"]),
            "edges_scanned": len(sample["edges"]),
            "counts": counts,
            "proposed_total": sum(counts.values()),
            "suppressed": (
                contradictions["suppressed"] + consolidation["suppressed"]
                + concepts["suppressed"] + links["suppressed"]
            ),
            "contradictions": contradictions,
            "concepts": concepts,
            "links": links,
            "consolidation": consolidation,
            "brief": self.brief_section(sample=sample, counts=counts),
            "trigger": self.trigger.status(),
            "generated_at": now_iso(),
        }

    # -- (b) higher-level concept proposals -------------------------------
    def _propose_concepts(
        self,
        sample: Dict[str, List[Dict[str, Any]]],
        workspace_id: Optional[str],
        user_email: Optional[str],
    ) -> Dict[str, Any]:
        clusters = _concept_clusters(sample["nodes"])
        desk = ProposalDesk(
            self._queue, user_email=user_email, workspace_id=workspace_id
        )
        open_keys = desk.open_keys()
        proposed: List[Dict[str, Any]] = []
        for cluster in clusters[:_MAX_PROPOSALS_PER_KIND]:
            titles = ", ".join(member["title"] for member in cluster["members"][:3])
            item = desk.propose(
                kind=CONCEPT_KIND,
                key=f"{CONCEPT_KIND}:{cluster['token']}",
                title=f"새 주제 후보: {cluster['token']}",
                summary=(
                    f"'{cluster['token']}'가 {cluster['size']}개의 기억에 반복해서 "
                    f"나타납니다({titles} 등). 하나의 주제로 묶어둘까요?"
                ),
                open_keys=open_keys,
                payload={
                    "token": cluster["token"],
                    "members": cluster["members"],
                    "size": cluster["size"],
                    "node_type": "Concept",
                },
            )
            if item is not None:
                proposed.append(item)
        return {
            "clusters": clusters,
            "proposed": proposed,
            "suppressed": desk.suppressed,
        }

    # -- (c) "always together, never linked" -------------------------------
    def _propose_links(
        self,
        sample: Dict[str, List[Dict[str, Any]]],
        workspace_id: Optional[str],
        user_email: Optional[str],
    ) -> Dict[str, Any]:
        pairs = _unlinked_pairs(sample["nodes"], sample["edges"])
        desk = ProposalDesk(
            self._queue, user_email=user_email, workspace_id=workspace_id
        )
        open_keys = desk.open_keys()
        proposed: List[Dict[str, Any]] = []
        for pair in pairs[:_MAX_PROPOSALS_PER_KIND]:
            item = desk.propose(
                kind=EDGE_KIND,
                key=_pair_key(EDGE_KIND, pair["left"]["id"], pair["right"]["id"]),
                title=(
                    f"연결 제안: {pair['left']['title']} ↔ {pair['right']['title']}"
                ),
                summary=(
                    f"'{pair['left']['title']}'와(과) '{pair['right']['title']}'는 "
                    f"자주 같이 등장하는데 아직 이어져 있지 않습니다"
                    f"(겹침 {int(pair['similarity'] * 100)}%). 연결할까요?"
                ),
                open_keys=open_keys,
                payload={
                    "source": pair["left"],
                    "target": pair["right"],
                    "edge_type": "RELATED_TO",
                    "similarity": pair["similarity"],
                    "shared_tokens": pair["shared_tokens"],
                },
            )
            if item is not None:
                proposed.append(item)
        return {"pairs": pairs, "proposed": proposed, "suppressed": desk.suppressed}

    # -- (a) weekly brief text --------------------------------------------
    def brief_section(
        self,
        *,
        sample: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        counts: Optional[Dict[str, int]] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Plain-language "what the Brain noticed" text for the Brain Brief.

        Deterministic by default. When a ``summarizer`` is injected the same
        facts are handed to it for nicer wording — and if it fails, the
        deterministic sentence stands. The model is never the source of the
        numbers.
        """
        if sample is None:
            sample = ProactiveBrain(self._store).sample(
                workspace_id=workspace_id, limit=self._sample_limit
            )
        counts = counts or {}
        window = _recent_window(sample["nodes"])
        headline = (
            f"최근 7일 동안 새 기억 {window['recent']}건이 쌓였고, "
            f"Brain이 검토할 거리 {sum(counts.values())}건을 찾았습니다."
        )
        lines = [
            f"모순되는 기억 {counts.get('contradictions', 0)}쌍",
            f"새 주제 후보 {counts.get('concepts', 0)}건",
            f"빠진 연결 {counts.get('links', 0)}건",
            f"정리할 오래된 기록 {counts.get('consolidation', 0)}묶음",
        ]
        return {
            "headline": self._summarize(headline, lines),
            "deterministic_headline": headline,
            "lines": lines,
            "recent_nodes": window["recent"],
            "window_days": window["days"],
            "counts": dict(counts),
            "generated_at": now_iso(),
        }

    def _summarize(self, headline: str, lines: Sequence[str]) -> str:
        if self._summarizer is None:
            return headline
        try:
            written = str(self._summarizer("\n".join([headline, *lines])) or "").strip()
        except Exception:  # noqa: BLE001 — a model may never break the brief
            logger.exception("synthesis summarizer failed")
            return headline
        return written or headline


def _recent_window(nodes: List[Dict[str, Any]], *, days: int = 7) -> Dict[str, int]:
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    recent = 0
    for node in nodes:
        text = str(node.get("updated_at") or "").strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            # The store writes naive *local* stamps (utils.now_iso); reading
            # them as UTC would shift the window by the machine's offset.
            parsed = parsed.astimezone()
        if parsed.timestamp() >= cutoff:
            recent += 1
    return {"recent": recent, "days": days}


def _concept_clusters(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tokens that recur across many memories but are nobody's topic yet.

    A token shared by at least three memories is a candidate; a token present
    in most of the sample is boilerplate, not a topic, and is dropped.
    """
    index: Dict[str, List[Dict[str, Any]]] = {}
    existing = {str(node.get("title") or "").strip().lower() for node in nodes}
    for node in nodes:
        text = _text_of(node)
        if len(text) < 3:
            continue
        for token in content_signature(text):
            index.setdefault(token, []).append(node)
    ceiling = max(_MIN_CLUSTER_MEMBERS, int(len(nodes) * _COMMON_TOKEN_RATIO))
    clusters: List[Dict[str, Any]] = []
    for token, members in index.items():
        if len(members) < _MIN_CLUSTER_MEMBERS or len(members) > ceiling:
            continue
        if token in existing:
            continue  # the topic node already exists
        clusters.append(
            {
                "token": token,
                "size": len(members),
                "members": [
                    {"id": str(m.get("id")), "title": _title_of(m)} for m in members[:8]
                ],
            }
        )
    clusters.sort(key=lambda item: (-item["size"], item["token"]))
    return clusters


def _unlinked_pairs(
    nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Memory pairs with strong token overlap and no edge between them."""
    linked = {
        _pair_key("e", str(edge.get("source") or ""), str(edge.get("target") or ""))
        for edge in edges
    }
    signatures = [
        (node, content_signature(_text_of(node)))
        for node in nodes
        if len(_text_of(node)) >= 3
    ]
    pairs: List[Dict[str, Any]] = []
    for index, (left, left_sig) in enumerate(signatures):
        for right, right_sig in signatures[index + 1:]:
            shared = left_sig & right_sig
            if len(shared) < _MIN_SHARED_TOKENS:
                continue
            left_id, right_id = str(left.get("id")), str(right.get("id"))
            if _pair_key("e", left_id, right_id) in linked:
                continue
            # Jaccard, same definition the proactive duplicate scorer uses.
            # The union cannot be empty here: `shared` already has 3 tokens.
            similarity = len(shared) / len(left_sig | right_sig)
            if similarity < _EDGE_SIMILARITY:
                continue
            pairs.append(
                {
                    "left": {"id": left_id, "title": _title_of(left)},
                    "right": {"id": right_id, "title": _title_of(right)},
                    "similarity": round(similarity, 4),
                    "shared_tokens": sorted(shared)[:8],
                }
            )
    pairs.sort(key=lambda item: (-item["similarity"], item["left"]["id"]))
    return pairs


__all__ = [
    "BrainSynthesizer",
    "CONCEPT_KIND",
    "CONSOLIDATION_KIND",
    "CONTRADICTION_KIND",
    "CONTRADICTION_RESOLUTIONS",
    "ContradictionResolutionError",
    "EDGE_KIND",
    "ProposalDesk",
    "SYNTHESIS_ENV",
    "SYNTHESIS_GATE",
    "SYNTHESIS_REVIEW_SOURCE",
    "SYNTHESIS_THRESHOLD_ENV",
    "SynthesisTrigger",
    "propose_consolidation",
    "propose_contradictions",
    "resolve_contradiction",
]
