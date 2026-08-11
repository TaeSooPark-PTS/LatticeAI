"""Windows, sample sizes, and the two readings every surface shares.

Leaf module: literals and pure functions only, no service and no store. The
health report, the insights digest, the garden and the contradiction scan all
measure against the same windows, which is what makes their numbers
comparable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)

_STALE_DAYS = 45
_RECENT_DAYS = 7
_GRAPH_SAMPLE_LIMIT = 800


def _parse_ts(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _no_graph_reason(graph_available: bool) -> str:
    """Why a graph-derived health dimension has nothing to say.

    Two different situations that both end in ``status: "unavailable"``: the
    graph could not be read at all, and the graph read fine but holds nothing
    yet. Telling them apart is the difference between "something is broken"
    and "you have not saved anything yet".
    """
    return (
        "no knowledge saved yet"
        if graph_available
        else "the knowledge graph could not be read"
    )
