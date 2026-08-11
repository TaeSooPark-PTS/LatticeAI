"""The memory tiers' vocabulary, the service's error, and the visual passthrough.

Everything here is a literal or a pure function over one recall row: no store,
no service, no I/O. Keeping it in one leaf module is what lets the store reads,
the manager, the brief and the maintenance surfaces share the same names
without importing each other.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

# Personal workspace memory kinds (from WorkspaceOS.MEMORY_KINDS).
WORKSPACE_KINDS = (
    "short_term",
    "workspace",
    "preferences",
    "decisions",
    "working_style",
    "frequently_used_tools",
    "long_term",
)

TIERS = ("workspace", "project", "agent", "conversation", "graph", "vector")
LOGGER = logging.getLogger(__name__)


#: Longest inline thumbnail a recall row will carry (see ``_visual_fields``).
MAX_RECALL_THUMBNAIL_CHARS = 24_000


def _visual_fields(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Caption + inline thumbnail for a recall row, when the node has them.

    v11.1.0: an ``Image`` node stores a 96px ``data:`` thumbnail and — only if
    a vision-language model produced one — a caption. Passing them through here
    is what lets the Evidence panel show the picture it is citing without a new
    static route over the user's disk and without going around the local-file
    approval gate: the bytes are already inside the graph payload the user
    asked for. Absent keys stay absent, so every non-image row is unchanged.
    """
    metadata = hit.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    fields: Dict[str, Any] = {}
    caption = str(metadata.get("caption") or "").strip()
    if caption:
        fields["caption"] = caption[:400]
    thumbnail = str(metadata.get("thumbnail") or "")
    if thumbnail.startswith("data:image/") and len(thumbnail) <= MAX_RECALL_THUMBNAIL_CHARS:
        fields["thumbnail"] = thumbnail
    return fields


class MemoryServiceError(RuntimeError):
    """Raised when a configured memory backend cannot be read reliably."""
