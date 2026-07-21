"""UX funnel metrics — lightweight runtime counters (backlog #16, review §4.3).

Tracks the product's core value funnel with honest, local-only counters:

* ``file_requests``          — chat requests recognized as file intents;
* ``real_file_delivered``    — file intents that produced actual artifacts;
* ``code_only_responses``    — file intents that finished with only a
  code/prose answer (the failure mode the >95% real-file goal watches);
* ``agent_runs``             — completed chat agent runs (rate denominator);
* ``needs_review_runs``      — agent runs that ended in ``NEEDS_REVIEW``;
* ``ingest_completions``     — successful ingestion pipeline completions;
* ``recall_successes``       — chat turns whose context was grounded in at
  least one Brain node.

TTFV ("time to first value") derives from two first-occurrence timestamps:
the first successful ingest and the first grounded recall/answer.

Design constraints (deliberate):

* **Cheap.** One JSON file under the data dir, atomic replace on write, a
  single ``threading.Lock`` — no background threads, no new dependencies.
* **Never breaks the product.** Every public method swallows I/O errors;
  metrics are advisory observability, not a gate.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

LOGGER = logging.getLogger(__name__)

COUNTER_NAMES = (
    "file_requests",
    "real_file_delivered",
    "code_only_responses",
    "agent_runs",
    "needs_review_runs",
    "ingest_completions",
    "recall_successes",
)

_FIRST_NAMES = ("first_ingest_at", "first_value_at")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rate(numerator: int, denominator: int) -> Optional[float]:
    """Honest rate: ``None`` (not 0.0) when there is no denominator yet."""
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


class FunnelMetricsService:
    """Thread-safe, JSON-persisted funnel counters."""

    def __init__(self, path: Any) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = self._load()

    # ── persistence ──────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        state: Dict[str, Any] = {name: 0 for name in COUNTER_NAMES}
        for name in _FIRST_NAMES:
            state[name] = None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return state
        except Exception as exc:  # noqa: BLE001 — corrupt metrics never break startup
            LOGGER.warning("funnel metrics load failed (%s); starting fresh", exc)
            return state
        if isinstance(raw, dict):
            for name in COUNTER_NAMES:
                try:
                    state[name] = max(0, int(raw.get(name) or 0))
                except (TypeError, ValueError):
                    state[name] = 0
            for name in _FIRST_NAMES:
                value = raw.get(name)
                state[name] = str(value) if value else None
        return state

    def _save_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=self._path.name, dir=str(self._path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self._state, handle, ensure_ascii=False, indent=2)
                os.replace(tmp_name, self._path)
            finally:
                if os.path.exists(tmp_name):
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
        except Exception as exc:  # noqa: BLE001 — metrics persistence is best-effort
            LOGGER.warning("funnel metrics save failed: %s", exc)

    # ── mutation ─────────────────────────────────────────────────────────

    def increment(self, name: str, by: int = 1) -> None:
        """Increment a known counter; unknown names are ignored (logged)."""
        if name not in COUNTER_NAMES:
            LOGGER.warning("funnel metrics: unknown counter %r ignored", name)
            return
        try:
            step = int(by)
        except (TypeError, ValueError):
            step = 1
        if step <= 0:
            return
        with self._lock:
            self._state[name] = int(self._state.get(name) or 0) + step
            self._save_locked()

    def _mark_first_locked(self, name: str) -> None:
        if not self._state.get(name):
            self._state[name] = _utc_now_iso()

    def record_ingest(self, *, duplicate: bool = False) -> None:
        """A successful ingestion completed; the first one starts the TTFV clock."""
        with self._lock:
            self._state["ingest_completions"] = (
                int(self._state.get("ingest_completions") or 0) + 1
            )
            self._mark_first_locked("first_ingest_at")
            self._save_locked()

    def record_recall_success(self) -> None:
        """A chat answer was grounded in the Brain; the first one ends TTFV."""
        with self._lock:
            self._state["recall_successes"] = (
                int(self._state.get("recall_successes") or 0) + 1
            )
            # First value only counts after knowledge actually entered the Brain.
            if self._state.get("first_ingest_at"):
                self._mark_first_locked("first_value_at")
            self._save_locked()

    # ── reads ────────────────────────────────────────────────────────────

    def ttfv_seconds(self) -> Optional[float]:
        with self._lock:
            first_ingest = _parse_iso(self._state.get("first_ingest_at"))
            first_value = _parse_iso(self._state.get("first_value_at"))
        if first_ingest is None or first_value is None:
            return None
        delta = (first_value - first_ingest).total_seconds()
        return round(delta, 1) if delta >= 0 else None

    def snapshot(self) -> Dict[str, Any]:
        """Counters + derived rates for the admin surface."""
        with self._lock:
            counters = {name: int(self._state.get(name) or 0) for name in COUNTER_NAMES}
            firsts = {name: self._state.get(name) for name in _FIRST_NAMES}
        return {
            "counters": counters,
            "firsts": firsts,
            "rates": {
                "real_file_rate": _rate(
                    counters["real_file_delivered"], counters["file_requests"]
                ),
                "code_only_rate": _rate(
                    counters["code_only_responses"], counters["file_requests"]
                ),
                "needs_review_rate": _rate(
                    counters["needs_review_runs"], counters["agent_runs"]
                ),
            },
            "ttfv_seconds": self.ttfv_seconds(),
            "generated_at": _utc_now_iso(),
        }


__all__ = ["FunnelMetricsService", "COUNTER_NAMES"]
