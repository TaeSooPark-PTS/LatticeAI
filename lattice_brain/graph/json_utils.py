"""JSON helpers shared by graph storage, projection, and retrieval modules."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional


def _json(data: Optional[Dict[str, Any]]) -> str:
    return json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def _safe_loads(raw: Optional[str]) -> Dict[str, Any]:
    """Tolerantly parse a metadata_json column — returns {} on corrupt rows."""
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (json.JSONDecodeError, TypeError) as e:
        logging.warning(
            "knowledge_graph: corrupt metadata_json (%s) — using empty dict", e
        )
        return {}
