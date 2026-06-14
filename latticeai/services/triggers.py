"""Trigger system (T7d) — workflows fire beyond 'manual'.

Two real trigger types:

* **interval** — a supervised scheduler loop fires the workflow every
  ``interval_seconds``. Firings missed while the server was down are
  SKIPPED with a recorded skip event (design-review amendment: no silent
  gaps, no thundering catch-up).
* **brain_event** — the killer Digital Brain feature: "when new knowledge
  enters the brain, run this workflow". Wired through the existing hooks
  bus (the ingestion pipeline fires ``post_tool`` on ``kg_ingest.<source>``);
  an optional ``source_type`` filter narrows it.

Trigger-fired runs carry provenance: their inputs include ``__trigger__``
describing what fired them, persisted in the run record like any other
input.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DEFAULT_TICK_SECONDS = 5.0
MIN_INTERVAL_SECONDS = 60

TRIGGER_HOOK_NAME = "brain-event-triggers"


class TriggerService:
    """Scans workflow definitions for non-manual triggers and fires them."""

    def __init__(
        self,
        *,
        store: Any,
        run_workflow: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        data_dir: Path,
        clock: Callable[[], float] = time.time,
        tick_seconds: float = DEFAULT_TICK_SECONDS,
    ) -> None:
        self._store = store
        self._run_workflow = run_workflow
        self._state_file = Path(data_dir) / "triggers_state.json"
        self._clock = clock
        self._tick = float(tick_seconds)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ── durable state ──────────────────────────────────────────────────────
    def _load_state(self) -> Dict[str, Any]:
        if not self._state_file.exists():
            return {}
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_file)

    def _record_event(self, state: Dict[str, Any], workflow_id: str, event: Dict[str, Any]) -> None:
        entry = state.setdefault(workflow_id, {})
        events = entry.setdefault("events", [])
        events.append({**event, "at": self._clock()})
        entry["events"] = events[-50:]

    # ── definition scanning ────────────────────────────────────────────────
    def _triggered_workflows(self) -> List[Dict[str, Any]]:
        found = []
        try:
            workflows = list(self._store.load_state().get("workflows") or [])
        except Exception:
            return []
        for wf in workflows:
            for node in wf.get("nodes") or []:
                if node.get("type") != "trigger":
                    continue
                cfg = node.get("config") or {}
                kind = str(cfg.get("trigger") or "manual")
                if cfg.get("enabled") is False:
                    continue
                if kind in ("interval", "brain_event"):
                    found.append({"workflow": wf, "node": node, "kind": kind, "config": cfg})
        return found

    def describe(self) -> Dict[str, Any]:
        """Honest status surface: what is armed, when it last fired/skipped."""
        state = self._load_state()
        armed = []
        for item in self._triggered_workflows():
            wf_id = item["workflow"].get("id")
            armed.append({
                "workflow_id": wf_id,
                "name": item["workflow"].get("name"),
                "kind": item["kind"],
                "config": {k: v for k, v in item["config"].items() if k != "trigger"},
                "last_fired_at": (state.get(wf_id) or {}).get("last_fired_at"),
                "recent_events": (state.get(wf_id) or {}).get("events", [])[-5:],
            })
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "tick_seconds": self._tick,
            "armed": armed,
        }

    # ── interval scheduling ────────────────────────────────────────────────
    def reconcile_missed(self) -> int:
        """Startup pass: record (never replay) firings missed while down."""
        now = self._clock()
        skipped = 0
        with self._lock:
            state = self._load_state()
            for item in self._triggered_workflows():
                if item["kind"] != "interval":
                    continue
                wf_id = item["workflow"].get("id")
                interval = max(MIN_INTERVAL_SECONDS, int(item["config"].get("interval_seconds") or 0))
                entry = state.setdefault(wf_id, {})
                last = entry.get("last_fired_at")
                if last is not None and now - float(last) > interval:
                    missed = int((now - float(last)) // interval)
                    self._record_event(state, wf_id, {
                        "type": "skipped",
                        "reason": f"{missed} interval firing(s) missed while the server was down",
                    })
                    skipped += missed
                # Reset the cadence from now — no catch-up storm.
                entry["last_fired_at"] = now if last is not None else entry.get("last_fired_at")
            self._save_state(state)
        return skipped

    def tick_intervals(self) -> int:
        """One scheduler pass; returns how many workflows fired."""
        now = self._clock()
        fired = 0
        with self._lock:
            state = self._load_state()
            for item in self._triggered_workflows():
                if item["kind"] != "interval":
                    continue
                wf_id = item["workflow"].get("id")
                interval = max(MIN_INTERVAL_SECONDS, int(item["config"].get("interval_seconds") or 0))
                entry = state.setdefault(wf_id, {})
                last = entry.get("last_fired_at")
                if last is None:
                    # First sighting arms the schedule; it fires one interval later.
                    entry["last_fired_at"] = now
                    continue
                if now - float(last) < interval:
                    continue
                entry["last_fired_at"] = now
                self._record_event(state, wf_id, {"type": "fired", "trigger": "interval"})
                fired += 1
                self._fire(wf_id, {
                    "type": "interval",
                    "interval_seconds": interval,
                    "fired_at": now,
                })
            self._save_state(state)
        return fired

    # ── brain events ───────────────────────────────────────────────────────
    def on_brain_event(self, event: str, payload: Optional[Dict[str, Any]] = None) -> int:
        """Fire workflows whose brain_event trigger matches this ingestion."""
        payload = payload or {}
        source_type = str(payload.get("source_type") or event.split(".", 1)[-1] or "")
        fired = 0
        with self._lock:
            state = self._load_state()
            for item in self._triggered_workflows():
                if item["kind"] != "brain_event":
                    continue
                wanted = str(item["config"].get("source_type") or "").strip()
                if wanted and wanted != source_type:
                    continue
                wf_id = item["workflow"].get("id")
                state.setdefault(wf_id, {})["last_fired_at"] = self._clock()
                self._record_event(state, wf_id, {
                    "type": "fired", "trigger": "brain_event", "source_type": source_type,
                })
                fired += 1
                self._fire(wf_id, {
                    "type": "brain_event",
                    "event": event,
                    "source_type": source_type,
                    "node_id": payload.get("node_id"),
                })
            self._save_state(state)
        return fired

    def hook_runner(self):
        """A post_tool hook runner: ingestion events fan into triggers."""
        def runner(context):
            event = str(getattr(context, "event", "") or "")
            if not event.startswith("kg_ingest."):
                return {"status": "ok", "output": "not an ingestion event"}
            payload = context.payload if isinstance(context.payload, dict) else {}
            fired = self.on_brain_event(event, payload)
            return {"status": "ok", "output": f"fired {fired} workflow trigger(s)"}
        return runner

    # ── execution + lifecycle ──────────────────────────────────────────────
    def _fire(self, workflow_id: str, trigger_info: Dict[str, Any]) -> None:
        def _run():
            try:
                self._run_workflow(workflow_id, {"__trigger__": trigger_info})
            except Exception as exc:
                logging.warning("trigger run failed for %s: %s", workflow_id, exc)

        threading.Thread(target=_run, name=f"trigger-{workflow_id}", daemon=True).start()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.reconcile_missed()
        self._stop_event.clear()

        def _loop():
            while not self._stop_event.wait(self._tick):
                try:
                    self.tick_intervals()
                except Exception as exc:
                    logging.warning("trigger scheduler tick failed: %s", exc)

        self._thread = threading.Thread(target=_loop, name="trigger-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)


__all__ = ["TriggerService", "TRIGGER_HOOK_NAME", "MIN_INTERVAL_SECONDS"]
