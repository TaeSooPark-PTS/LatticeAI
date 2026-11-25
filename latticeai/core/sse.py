from __future__ import annotations

import json
from typing import Any, Optional

__all__ = ["sse_frame"]


def sse_frame(event: Optional[str], data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {payload}\n\n"
