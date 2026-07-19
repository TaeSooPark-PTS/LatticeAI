"""Structured observability for the single-agent reasoning loop (v9.6.0).

The PLAN → EXECUTE → VERIFY state machine in :mod:`latticeai.core.agent`
already keeps a human-readable transcript, but the transcript mixes model
output, tool results, and control decisions into one list — you cannot ask
"how many parse failures were recovered?" or "which repairs did the weak
model need?" without re-parsing it.

:class:`LoopTrace` is the machine-readable side channel: every phase records
typed events (llm_call, parse_error, repair, correction, tool call outcome,
blocked action, retry, rollback), and :meth:`LoopTrace.summary` reduces them
to the counters an evaluation harness or the API response can consume
directly. The trace is pure data — no I/O, no clock dependency beyond an
injectable timestamp function — so unit tests and the deterministic agent
evaluation harness can assert on it exactly.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from latticeai.core.timeutil import now_iso

_MAX_EVENTS = 500


class LoopTrace:
    """Typed event stream + summary counters for one agent run."""

    def __init__(self, clock: Optional[Callable[[], str]] = None) -> None:
        self._clock = clock or now_iso
        self.events: List[Dict[str, Any]] = []
        self.truncated = 0

    def record(self, phase: str, kind: str, **details: Any) -> None:
        if len(self.events) >= _MAX_EVENTS:
            self.truncated += 1
            return
        event: Dict[str, Any] = {"phase": phase, "kind": kind, "at": self._clock()}
        for key, value in details.items():
            if value is not None:
                event[key] = value
        self.events.append(event)

    # ── typed helpers keep call sites terse and the vocabulary closed ────

    def llm_call(self, phase: str, *, model: Optional[str] = None) -> None:
        self.record(phase, "llm_call", model=model)

    def parse_error(self, phase: str, *, error: str, recovered: bool) -> None:
        self.record(phase, "parse_error", error=error[:200], recovered=recovered)

    def repair(self, phase: str, *, repairs: List[str]) -> None:
        if repairs:
            self.record(phase, "repair", repairs=list(repairs))

    def correction(self, phase: str, *, hint: str) -> None:
        self.record(phase, "correction", hint=hint[:200])

    def tool(self, phase: str, *, name: str, outcome: str, risk: Optional[str] = None) -> None:
        self.record(phase, "tool", name=name, outcome=outcome, risk=risk)

    def decision(self, phase: str, *, decision: str, **details: Any) -> None:
        self.record(phase, "decision", decision=decision, **details)

    def retry(self, phase: str, *, attempt: int) -> None:
        self.record(phase, "retry", attempt=attempt)

    # ── reduction ────────────────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        tool_outcomes: Dict[str, int] = {}
        repairs: Dict[str, int] = {}
        parse_errors = 0
        parse_recovered = 0
        for event in self.events:
            kind = event["kind"]
            counts[kind] = counts.get(kind, 0) + 1
            if kind == "tool":
                outcome = str(event.get("outcome") or "unknown")
                tool_outcomes[outcome] = tool_outcomes.get(outcome, 0) + 1
            elif kind == "parse_error":
                parse_errors += 1
                if event.get("recovered"):
                    parse_recovered += 1
            elif kind == "repair":
                for name in event.get("repairs") or []:
                    repairs[name] = repairs.get(name, 0) + 1
        return {
            "events": len(self.events),
            "truncated_events": self.truncated,
            "kind_counts": counts,
            "llm_calls": counts.get("llm_call", 0),
            "parse_errors": parse_errors,
            "parse_recovered": parse_recovered,
            "corrections": counts.get("correction", 0),
            "retries": counts.get("retry", 0),
            "tool_outcomes": tool_outcomes,
            "repairs": repairs,
        }


__all__ = ["LoopTrace"]
