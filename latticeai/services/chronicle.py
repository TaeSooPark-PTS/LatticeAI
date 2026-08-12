"""Brain Chronicle — the Brain's own timeline (v11.3.0, track B).

v11.1.0 gave every memory a validity window (``valid_from``/``valid_to``/
``superseded_by``), recorded each sighting of a relationship in
``edge_occurrences``, and shipped ``store.as_of()``. None of it had a reader:
the promise "your Brain remembers *when*" was true in the database and
invisible in the product.

This service is that reader, and nothing more. Three answers, all derived
from rows that already exist:

* :meth:`overview` — how the Brain grew, one bucket per day.
* :meth:`day` — what actually happened on one day, grouped the way a person
  would tell it: what came in, what was learned, what was talked about, what
  changed its mind.
* :meth:`as_of` — the Brain as it stood at an instant, read through
  ``store.as_of()`` so the temporal predicate has exactly one implementation.

Three rules hold the whole module:

**Read-only.** No writes, no schema changes, no model calls. Every number is
a re-arrangement of stored facts, so the chronicle can never invent a memory.

**One timezone.** Day buckets come from :mod:`latticeai.core.timezones`, the
same helper the audit log and "events today" already use. A stamp that
carries an offset is converted into that zone; a naive stamp is taken as
already being in it, because that is what the store writes
(``lattice_brain.utils.now_iso``). Without this, a Seoul user's midnight
uploads would file themselves under yesterday.

**Workspace scoping, fail-closed.** ``workspace_id=None`` means the unscoped
single-user read (what ``CommandCenterService`` expresses as
``allowed_workspaces=None``); a workspace id restricts every lane to that
workspace and *excludes* legacy-global rows, exactly as
``_scope_kwargs`` does there. The predicate is bound as a parameter rather
than interpolated, so there is no query in this module whose text depends on
input.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import date as date_type
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from latticeai.core.timeutil import parse_iso
from latticeai.core.timezones import get_timezone
from latticeai.core.workspace_os_utils import graph_scope_kwargs

LOGGER = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]*>")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")

#: Conversation previews are one line, plain text, and short. The chronicle
#: renders them as cards, never as HTML.
_PREVIEW_LIMIT = 140

#: A single day can carry thousands of rows. ``counts`` stays truthful about
#: the whole day; the listed items stop here so one busy day cannot return an
#: unbounded payload.
_GROUP_LIMIT = 200

#: "What was important then" is a short list by construction.
_TOP_ENTITY_LIMIT = 12

#: ``store.as_of()`` clamps its own limit at 2000. Asking for the ceiling
#: keeps the rewind slice as complete as the store is willing to return.
_AS_OF_LIMIT = 2000

_SOURCES_SQL = """
    SELECT id,
           node_id,
           COALESCE(title, '') AS title,
           source_type,
           COALESCE(captured_at, created_at) AS at
    FROM ingestion_provenance
    WHERE (:workspace IS NULL OR workspace_id = :workspace)
    ORDER BY at ASC, id ASC
"""

# The excluded types are containers of raw material rather than things the
# Brain learned: they are already counted in the ``sources`` and
# ``conversations`` lanes, and counting them again as "concepts" would make
# every upload look like two memories. ``nodes_v2.type`` is always the
# canonical upper-case type; ``legacy_type`` keeps the label the writer used,
# which is what a reader should see.
_ENTITIES_SQL = """
    SELECT id,
           label,
           COALESCE(NULLIF(legacy_type, ''), type) AS type,
           created_at AS at
    FROM nodes_v2
    WHERE (:workspace IS NULL OR workspace_id = :workspace)
      AND type NOT IN ('DOCUMENT', 'CHUNK', 'FILE', 'MESSAGE', 'CONVERSATION')
    ORDER BY at ASC, id ASC
"""

# A relationship is filed under the day it was first *observed*
# (``edge_occurrences.observed_at``), falling back to the row's own
# ``created_at`` for edges written by paths that record no occurrence.
_CONNECTIONS_SQL = """
    SELECT e.id AS id,
           COALESCE(MIN(o.observed_at), e.created_at) AS at
    FROM edges_v2 e
    LEFT JOIN edge_occurrences o ON o.edge_id = e.id
    WHERE e.source IN (
            SELECT id FROM nodes_v2
            WHERE (:workspace IS NULL OR workspace_id = :workspace))
      AND e.target IN (
            SELECT id FROM nodes_v2
            WHERE (:workspace IS NULL OR workspace_id = :workspace))
    GROUP BY e.id, e.created_at
    ORDER BY at ASC, e.id ASC
"""

# Rows with no ``user_email`` are pre-auth / single-user history: excluding
# them would empty the chronicle of every conversation a solo Brain ever had.
# This is the same allowance ``ConversationStore._scope_sql`` makes.
_MESSAGES_SQL = """
    SELECT COALESCE(conversation_id, '') AS conversation_id,
           role,
           content,
           timestamp AS at
    FROM conversation_messages
    WHERE (:workspace IS NULL OR workspace_id = :workspace)
      AND (:user IS NULL OR user_email = :user
           OR user_email IS NULL OR user_email = '')
    ORDER BY id ASC
"""

_CHANGED_NODES_SQL = """
    SELECT id,
           label,
           superseded_by,
           COALESCE(valid_to, updated_at) AS at
    FROM nodes_v2
    WHERE (:workspace IS NULL OR workspace_id = :workspace)
      AND (valid_to IS NOT NULL OR superseded_by IS NOT NULL)
    ORDER BY at ASC, id ASC
"""

# ``edges_v2`` has no ``updated_at``, so an edge that was superseded without
# a ``valid_to`` carries no instant to file the change under. Rather than
# inventing one from ``created_at`` — which would date the change to when the
# relationship was *made* — such a row is left out and said so here.
#
# No product path stamps an edge's ``valid_to`` yet (v11.3.0), so this lane is
# empty on an ordinary Brain, and that zero is honest rather than hidden. It
# is read anyway because ``store.as_of()`` already honours the column: an edge
# outside its window disappears from a rewind, and a chronicle that ignored
# the same column would contradict the panel sitting next to it.
_CHANGED_EDGES_SQL = """
    SELECT e.id AS id,
           e.source AS node_id,
           s.label AS source_label,
           t.label AS target_label,
           e.superseded_by AS superseded_by,
           e.valid_to AS at
    FROM edges_v2 e
    JOIN nodes_v2 s ON s.id = e.source
    JOIN nodes_v2 t ON t.id = e.target
    WHERE (:workspace IS NULL OR s.workspace_id = :workspace)
      AND (:workspace IS NULL OR t.workspace_id = :workspace)
      AND e.valid_to IS NOT NULL
    ORDER BY at ASC, e.id ASC
"""

_LANES = ("sources", "entities", "connections", "conversations")


# ── time ────────────────────────────────────────────────────────────────────


def _local(moment: datetime) -> datetime:
    """``moment`` as naive wall-clock time in the configured timezone."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(get_timezone()).replace(tzinfo=None)


def _moment(value: Any) -> Optional[datetime]:
    """Parse a stored stamp, or ``None`` when it is unreadable.

    Unreadable stamps exist: the conversation store writes
    ``str(item.get("timestamp") or "")``, so an import with no timestamp
    lands as an empty string. Such a row has no day to belong to and is
    dropped from every count rather than being filed under today.
    """
    parsed = parse_iso(str(value or "").strip())
    if parsed is None:
        return None
    return _local(parsed)


def _day_of(value: Any) -> Optional[str]:
    """``YYYY-MM-DD`` in the configured timezone, or ``None``."""
    moment = _moment(value)
    if moment is None:
        return None
    return moment.date().isoformat()


def parse_day(value: Any) -> str:
    """Validate a ``YYYY-MM-DD`` path segment, returning it normalized.

    Raises ``ValueError`` for anything else — including a well-shaped but
    impossible date such as ``2026-13-45`` — which the router turns into 422.
    """
    text = str(value or "").strip()
    if not _DAY.fullmatch(text):
        raise ValueError(f"chronicle date must be YYYY-MM-DD: {value!r}")
    try:
        parsed = date_type.fromisoformat(text)
    except ValueError:
        raise ValueError(f"chronicle date is not a real date: {value!r}") from None
    return parsed.isoformat()


def parse_timestamp(value: Any) -> str:
    """Normalize an ISO-8601 instant into the store's own stamp format.

    The store writes naive local seconds, so an offset-aware input is moved
    into the configured timezone *here* rather than being handed to
    ``store.as_of()`` with a suffix no stored row carries.
    """
    parsed = parse_iso(str(value or "").strip())
    if parsed is None:
        raise ValueError(f"chronicle timestamp must be ISO-8601: {value!r}")
    return _local(parsed).isoformat(timespec="seconds")


# ── shaping ─────────────────────────────────────────────────────────────────


def _preview(text: Any) -> str:
    """First non-empty line, tags stripped, whitespace collapsed, truncated."""
    flat = _TAG.sub(" ", str(text or ""))
    first = next((line for line in (raw.strip() for raw in flat.splitlines()) if line), "")
    collapsed = " ".join(first.split())
    if len(collapsed) > _PREVIEW_LIMIT:
        return collapsed[: _PREVIEW_LIMIT - 1].rstrip() + "…"
    return collapsed


def _empty_lanes() -> Dict[str, int]:
    return {lane: 0 for lane in _LANES}



#: Historical module-local name for the shared rule.
_scope_kwargs = graph_scope_kwargs


def _on_day(rows: Sequence[Any], day: str) -> List[Any]:
    return [row for row in rows if _day_of(row["at"]) == day]


def _conversation_cards(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    """One card per conversation that had messages on the day.

    Messages with no ``conversation_id`` (imported legacy history) share the
    empty id, so a day's loose messages become one card instead of vanishing.
    """
    grouped: Dict[str, List[Any]] = {}
    for row in rows:
        grouped.setdefault(str(row["conversation_id"]), []).append(row)
    cards: List[Dict[str, Any]] = []
    for conversation_id, items in grouped.items():
        asked = [item for item in items if item["role"] == "user"]
        lead = asked[0] if asked else items[0]
        cards.append(
            {
                "conversation_id": conversation_id,
                "preview": _preview(lead["content"]),
                "messages": len(items),
                "started_at": items[0]["at"],
            }
        )
    return cards


def _change_cards(nodes: Sequence[Any], edges: Sequence[Any]) -> List[Dict[str, Any]]:
    """What stopped being true on the day, facts first then relationships.

    A resolved contradiction lands here without a table of its own:
    ``lattice_brain.synthesis.resolve_contradiction`` settles a pair by
    stamping ``valid_to`` (and ``superseded_by`` for replace/keep_old) on
    ``nodes_v2``, which is precisely what ``_CHANGED_NODES_SQL`` selects.
    """
    cards: List[Dict[str, Any]] = []
    for row in nodes:
        cards.append(
            {
                "kind": "fact_superseded" if row["superseded_by"] else "fact_retired",
                "label": row["label"],
                "at": row["at"],
                "node_id": row["id"],
            }
        )
    for row in edges:
        kind = "connection_superseded" if row["superseded_by"] else "connection_ended"
        cards.append(
            {
                "kind": kind,
                "label": f"{row['source_label']} → {row['target_label']}",
                "at": row["at"],
                "node_id": row["node_id"],
            }
        )
    cards.sort(key=lambda card: (str(card["at"]), str(card["node_id"])))
    return cards


class ChronicleService:
    """Read-only timeline over the Brain's own storage."""

    def __init__(
        self,
        *,
        knowledge_graph: Any = None,
        conversations: Any = None,
        enable_graph: bool = True,
    ) -> None:
        self._kg = knowledge_graph
        self._conversations = conversations
        self._enable_graph = bool(enable_graph and knowledge_graph is not None)

    # ── storage ─────────────────────────────────────────────────────────

    @property
    def _graph_db(self) -> Any:
        return getattr(self._kg, "db_path", None) if self._enable_graph else None

    @property
    def _conversation_db(self) -> Any:
        return getattr(self._conversations, "db_path", None)

    @staticmethod
    def _rows(db_path: Any, sql: str, params: Mapping[str, Any]) -> List[Any]:
        """One read query. An unreachable database answers "nothing".

        A Brain that was never built has no ``nodes_v2``, and a graph-disabled
        install has no graph database at all. Either way the chronicle shows
        an honest empty timeline instead of a 500.
        """
        if db_path is None:
            return []
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            return list(conn.execute(sql, params).fetchall())
        except sqlite3.Error:
            LOGGER.exception("chronicle read failed against %s", db_path)
            return []
        finally:
            if conn is not None:
                conn.close()

    def _sources(self, workspace_id: Optional[str]) -> List[Any]:
        return self._rows(self._graph_db, _SOURCES_SQL, {"workspace": workspace_id})

    def _entities(self, workspace_id: Optional[str]) -> List[Any]:
        return self._rows(self._graph_db, _ENTITIES_SQL, {"workspace": workspace_id})

    def _connections(self, workspace_id: Optional[str]) -> List[Any]:
        return self._rows(self._graph_db, _CONNECTIONS_SQL, {"workspace": workspace_id})

    def _changed_nodes(self, workspace_id: Optional[str]) -> List[Any]:
        return self._rows(self._graph_db, _CHANGED_NODES_SQL, {"workspace": workspace_id})

    def _changed_edges(self, workspace_id: Optional[str]) -> List[Any]:
        return self._rows(self._graph_db, _CHANGED_EDGES_SQL, {"workspace": workspace_id})

    def _messages(
        self, user_email: Optional[str], workspace_id: Optional[str]
    ) -> List[Any]:
        return self._rows(
            self._conversation_db,
            _MESSAGES_SQL,
            # An empty email is nobody, not a user whose address is "".
            {"workspace": workspace_id, "user": (user_email or "").strip() or None},
        )

    # ── overview ────────────────────────────────────────────────────────

    def overview(
        self,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Day buckets for the whole history, plus totals and the endpoints.

        ``series`` is sparse: only days that carry something appear, because a
        Brain with a two-year gap should not answer with 700 rows of zeros.
        The growth curve reads it as steps; the heat-map fills its own grid.

        ``first_activity_at``/``last_activity_at`` are normalized into the
        configured timezone rather than echoed raw, so two rows written in
        different formats stay comparable to each other and to ``series``.
        """
        days: Dict[str, Dict[str, int]] = {}
        totals = _empty_lanes()
        moments: List[datetime] = []
        lanes: Tuple[Tuple[str, Iterable[Any]], ...] = (
            ("sources", self._sources(workspace_id)),
            ("entities", self._entities(workspace_id)),
            ("connections", self._connections(workspace_id)),
        )
        for lane, rows in lanes:
            for row in rows:
                moment = _moment(row["at"])
                if moment is None:
                    continue
                moments.append(moment)
                totals[lane] += 1
                days.setdefault(moment.date().isoformat(), _empty_lanes())[lane] += 1

        per_day: Dict[str, set] = {}
        seen: set = set()
        for row in self._messages(user_email, workspace_id):
            moment = _moment(row["at"])
            if moment is None:
                continue
            moments.append(moment)
            key = moment.date().isoformat()
            days.setdefault(key, _empty_lanes())
            per_day.setdefault(key, set()).add(str(row["conversation_id"]))
            seen.add(str(row["conversation_id"]))
        for key, conversation_ids in per_day.items():
            days[key]["conversations"] = len(conversation_ids)
        totals["conversations"] = len(seen)

        return {
            "first_activity_at": min(moments).isoformat(timespec="seconds") if moments else None,
            "last_activity_at": max(moments).isoformat(timespec="seconds") if moments else None,
            "totals": totals,
            "series": [{"date": key, **days[key]} for key in sorted(days)],
        }

    # ── one day ─────────────────────────────────────────────────────────

    def day(
        self,
        date: Any,
        *,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """What entered, formed, was said, and changed on one calendar day.

        A day with nothing in it is a real answer: every group comes back
        empty rather than 404, because "nothing happened" is a fact about the
        Brain and the screen says so.
        """
        day = parse_day(date)
        sources = _on_day(self._sources(workspace_id), day)
        entities = _on_day(self._entities(workspace_id), day)
        messages = _on_day(self._messages(user_email, workspace_id), day)
        conversations = _conversation_cards(messages)
        changes = _change_cards(
            _on_day(self._changed_nodes(workspace_id), day),
            _on_day(self._changed_edges(workspace_id), day),
        )
        return {
            "date": day,
            "counts": {
                "sources": len(sources),
                "entities": len(entities),
                "conversations": len(conversations),
                "changes": len(changes),
            },
            "groups": {
                "sources": [
                    {
                        "id": row["id"],
                        "title": row["title"],
                        "source_type": row["source_type"],
                        "captured_at": row["at"],
                        "node_id": row["node_id"],
                    }
                    for row in sources[:_GROUP_LIMIT]
                ],
                "entities": [
                    {
                        "id": row["id"],
                        "label": row["label"],
                        "type": row["type"],
                        "created_at": row["at"],
                    }
                    for row in entities[:_GROUP_LIMIT]
                ],
                "conversations": conversations[:_GROUP_LIMIT],
                "changes": changes[:_GROUP_LIMIT],
            },
        }

    # ── rewind ──────────────────────────────────────────────────────────

    def _importance(self, node_ids: Sequence[str]) -> Dict[str, float]:
        """``importance_score`` per node, read through the store's own API."""
        if not node_ids:
            return {}
        stats = self._kg.access_stats(node_ids)
        return {
            str(key): float((value or {}).get("accesses") or 0.0)
            for key, value in stats.items()
        }

    def as_of(self, ts: Any, *, workspace_id: Optional[str] = None) -> Dict[str, Any]:
        """The Brain as it stood at ``ts``.

        The slice comes from ``store.as_of()``: the ``[valid_from, valid_to)``
        predicate has one implementation in this repo and it is not this one.
        ``stats`` therefore reports what that slice contains — the store
        clamps at 2000 nodes, which is the ceiling asked for here.
        """
        stamp = parse_timestamp(ts)
        if not self._enable_graph:
            return {
                "ts": stamp,
                "stats": {"entities": 0, "connections": 0},
                "top_entities": [],
            }
        window = self._kg.as_of(stamp, limit=_AS_OF_LIMIT, **_scope_kwargs(workspace_id))
        nodes = list(window.get("nodes") or [])
        scores = self._importance([str(node.get("id")) for node in nodes])
        ranked = sorted(
            nodes,
            key=lambda node: (-scores.get(str(node.get("id")), 0.0), str(node.get("id"))),
        )
        return {
            "ts": stamp,
            "stats": {
                "entities": int(window.get("node_count") or 0),
                "connections": int(window.get("edge_count") or 0),
            },
            "top_entities": [
                {
                    "id": node.get("id"),
                    "label": node.get("title"),
                    "type": node.get("type"),
                    "importance_score": scores.get(str(node.get("id")), 0.0),
                }
                for node in ranked[:_TOP_ENTITY_LIMIT]
            ],
        }


__all__ = ["ChronicleService", "parse_day", "parse_timestamp"]
