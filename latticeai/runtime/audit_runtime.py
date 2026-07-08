"""Audit log wiring extracted from app_factory _build for smaller closure.

Returns dict of get_audit_log / append_audit_event for legacy namespace.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from latticeai.core import timezones


def build_audit_runtime(
    *,
    audit_file: Path,
    logging: Any,
    redact_fn: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    _audit_lock: Any = __import__("threading").Lock()
    audit_jsonl = audit_file.with_suffix(audit_file.suffix + ".jsonl")

    def _read_audit() -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        if not audit_file.exists():
            data = []
        else:
            try:
                data = json.loads(audit_file.read_text(encoding="utf-8"))
            except Exception:
                data = []
        if isinstance(data, list):
            events.extend(item for item in data if isinstance(item, dict))
        if audit_jsonl.exists():
            try:
                for line in audit_jsonl.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if isinstance(item, dict):
                        events.append(item)
            except Exception:
                pass
        return events[-5000:]

    def _append(event_type: str, **payload: Any) -> None:
        entry = {"event_type": event_type, "timestamp": timezones.now_iso()}
        if payload:
            entry.update(payload)
        if redact_fn:
            try:
                for k in ("content", "content_preview", "message"):
                    if k in entry and isinstance(entry[k], str):
                        entry[k] = redact_fn(entry[k])
            except Exception:
                pass
        with _audit_lock:
            audit_file.parent.mkdir(parents=True, exist_ok=True)
            with audit_jsonl.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False, default=str))
                fh.write("\n")

    def get_audit_log() -> List[Dict[str, Any]]:
        return _read_audit()

    def append_audit_event(event_type: str, **payload: Any) -> None:
        try:
            _append(event_type, **payload)
        except Exception as e:
            logging.warning("audit append failed: %s", e)

    return {
        "get_audit_log": get_audit_log,
        "append_audit_event": append_audit_event,
    }
