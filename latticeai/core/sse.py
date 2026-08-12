"""The one Server-Sent Events frame builder.

Five modules each formatted their own frame — three in the
``event:``/``data:`` shape and two in the bare ``data:`` shape — with the same
``json.dumps(..., ensure_ascii=False)`` payload. They agreed by accident, and a
frame that disagrees by one byte is a stream the browser silently stops
parsing, so the agreement is now structural rather than coincidental.

``event=None`` produces the bare shape; a named event produces the prefixed
one. Both are byte-identical to the copies they replaced.
"""

from __future__ import annotations

import json
from typing import Any, Optional

__all__ = ["sse_frame"]


def sse_frame(event: Optional[str], data: Any) -> str:
    """Render one SSE frame, terminated by the blank line that ends it."""
    payload = json.dumps(data, ensure_ascii=False)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {payload}\n\n"
