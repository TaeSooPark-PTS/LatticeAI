"""Shared runtime contracts for agent execution surfaces.

These contracts are deliberately small and serializable.  The single-agent
state machine and the multi-agent facade can evolve independently, but callers
should see the same run identity, mode, status, role, timeline and artifact
shape across both paths.

Contract family (7.4.0)
-----------------------
Four observability surfaces — agent runs, workflow runs, audit events, and
realtime events — now share one **contract family**, ``agent-run-contract/v1``.
Every record emitted by any of these surfaces carries the same envelope keys so
a single consumer (timeline UI, exporter, replay tooling) can treat them
uniformly:

* ``family``         — always :data:`CONTRACT_FAMILY` (``agent-run-contract/v1``).
* ``schema_version`` — the per-surface schema string (see ``*_SCHEMA`` below).
* ``kind``           — one of :data:`CONTRACT_KINDS`.
* ``id``             — the record identity (run id / event id), may be ``None``.
* ``status``         — a normalized lifecycle status string.
* ``timestamp``      — ISO-8601 second-precision emit/observe time.

Each surface keeps its own rich, surface-specific keys *in addition* to the
envelope — the envelope is purely additive, so existing consumers are never
broken.  :func:`stamp_contract` is the single place that writes the envelope;
:func:`is_contract_member` validates membership for tests and importers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Protocol, runtime_checkable


def runtime_timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── Contract family identity ────────────────────────────────────────────────
# A single family string ties every observability surface together. Per-surface
# schema versions live *under* the family so each surface can rev independently
# without forking the family contract consumers depend on.
CONTRACT_FAMILY = "agent-run-contract/v1"

AGENT_RUN_SCHEMA = "agent-run-contract/v1"
WORKFLOW_RUN_SCHEMA = "workflow-run-contract/v1"
AUDIT_EVENT_SCHEMA = "audit-event-contract/v1"
REALTIME_EVENT_SCHEMA = "realtime-event-contract/v1"

CONTRACT_KINDS = ("agent_run", "workflow_run", "audit_event", "realtime_event")

# The envelope keys every family member is guaranteed to expose.
CONTRACT_ENVELOPE_KEYS = ("family", "schema_version", "kind", "id", "status", "timestamp")
CONTRACT_VIEW_KEYS = (
    "family",
    "schema_version",
    "kind",
    "id",
    "run_id",
    "agent_id",
    "runtime",
    "mode",
    "status",
    "goal",
    "current_role",
    "timestamp",
    "is_terminal",
    "blocking_reasons",
)

RUNTIME_BOUNDARY_SCHEMA = "runtime-boundary/v1"

_SCHEMA_FOR_KIND = {
    "agent_run": AGENT_RUN_SCHEMA,
    "workflow_run": WORKFLOW_RUN_SCHEMA,
    "audit_event": AUDIT_EVENT_SCHEMA,
    "realtime_event": REALTIME_EVENT_SCHEMA,
}


def stamp_contract(
    body: Dict[str, Any],
    *,
    kind: str,
    identity: Optional[str],
    status: str,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Return ``body`` with the ``agent-run-contract/v1`` envelope merged in.

    Additive and idempotent: existing keys in ``body`` win for everything except
    the six envelope keys, which are authoritative. ``kind`` must be one of
    :data:`CONTRACT_KINDS`; the schema version is derived from it so callers
    cannot mismatch ``kind`` and ``schema_version``.
    """
    if kind not in _SCHEMA_FOR_KIND:
        raise ValueError(f"unknown contract kind: {kind!r}")
    enveloped = dict(body)
    enveloped["family"] = CONTRACT_FAMILY
    enveloped["schema_version"] = _SCHEMA_FOR_KIND[kind]
    enveloped["kind"] = kind
    enveloped["id"] = identity
    enveloped["status"] = status
    enveloped["timestamp"] = timestamp or body.get("timestamp") or runtime_timestamp()
    return enveloped


def is_contract_member(record: Any) -> bool:
    """True if ``record`` carries a valid ``agent-run-contract/v1`` envelope."""
    if not isinstance(record, dict):
        return False
    if record.get("family") != CONTRACT_FAMILY:
        return False
    kind = record.get("kind")
    if kind not in _SCHEMA_FOR_KIND:
        return False
    if record.get("schema_version") != _SCHEMA_FOR_KIND[kind]:
        return False
    return all(key in record for key in CONTRACT_ENVELOPE_KEYS)


def extract_contract(record: Any) -> Optional[Dict[str, Any]]:
    """Return the normalized family contract carried by ``record``.

    Consumers should call this instead of branching on whether they received an
    agent run, workflow run, audit row, or realtime event. The function accepts
    either a raw contract dict or a surface record with a nested ``contract``.
    """
    if is_contract_member(record):
        return dict(record)
    if isinstance(record, dict) and is_contract_member(record.get("contract")):
        return dict(record["contract"])
    return None


def require_contract(record: Any) -> Dict[str, Any]:
    """Return a valid family contract or raise a precise error."""
    contract = extract_contract(record)
    if contract is None:
        raise ValueError("record is missing an agent-run-contract/v1 family contract")
    return contract


def contract_view(record: Any) -> Dict[str, Any]:
    """Return a compact, surface-agnostic view for API and UI consumers."""
    contract = require_contract(record)
    return {key: contract.get(key) for key in CONTRACT_VIEW_KEYS if key in contract}


def contract_views(records: Iterable[Any]) -> List[Dict[str, Any]]:
    """Return compact views for every valid contract in ``records``."""
    views: List[Dict[str, Any]] = []
    for record in records:
        contract = extract_contract(record)
        if contract is not None:
            views.append({key: contract.get(key) for key in CONTRACT_VIEW_KEYS if key in contract})
    return views


@dataclass(frozen=True)
class AgentRunContract:
    run_id: Optional[str]
    agent_id: str
    runtime: str
    mode: str
    status: str
    goal: str
    roles: List[str] = field(default_factory=list)
    current_role: Optional[str] = None
    retries: int = 0
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    blocking_reasons: List[str] = field(default_factory=list)
    schema_version: str = "agent-run-contract/v1"

    @property
    def is_terminal(self) -> bool:
        return self.status in {"ok", "retried_ok", "failed", "rejected", "cancelled", "interrupted", "partial", "done"}

    def as_dict(self) -> Dict[str, Any]:
        body = {
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "runtime": self.runtime,
            "mode": self.mode,
            "goal": self.goal,
            "roles": list(self.roles),
            "current_role": self.current_role,
            "retries": self.retries,
            "timeline": list(self.timeline),
            "artifacts": list(self.artifacts),
            "blocking_reasons": list(self.blocking_reasons),
            "is_terminal": self.is_terminal,
        }
        return stamp_contract(
            body,
            kind="agent_run",
            identity=self.run_id,
            status=self.status,
        )


def multi_agent_contract(
    *,
    result: Any,
    goal: str,
    run_id: Optional[str] = None,
    current_role: Optional[str] = None,
    blocking_reasons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return AgentRunContract(
        run_id=run_id,
        agent_id=getattr(result, "agent_id", "agent:executor"),
        runtime="multi_agent",
        mode=getattr(result, "mode", "simulation"),
        status=getattr(result, "status", "unknown"),
        goal=goal,
        roles=list(getattr(result, "roles_run", []) or []),
        current_role=current_role,
        retries=int(getattr(result, "retries", 0) or 0),
        timeline=list(getattr(result, "timeline", []) or []),
        artifacts=[],
        blocking_reasons=list(blocking_reasons or []),
    ).as_dict()


@dataclass(frozen=True)
class RuntimeBoundaryContract:
    """Machine-readable descriptor for an execution runtime boundary.

    This is intentionally separate from run records: it lets tests, routers, and
    docs assert which class owns which execution surface without importing the
    wrong runtime by name.
    """

    name: str
    runtime: str
    entrypoint: str
    surface: str
    owns: str
    compatibility_aliases: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": RUNTIME_BOUNDARY_SCHEMA,
            "name": self.name,
            "runtime": self.runtime,
            "entrypoint": self.entrypoint,
            "surface": self.surface,
            "owns": self.owns,
            "compatibility_aliases": list(self.compatibility_aliases),
        }


def runtime_boundary_contract(
    *,
    name: str,
    runtime: str,
    entrypoint: str,
    surface: str,
    owns: str,
    compatibility_aliases: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return RuntimeBoundaryContract(
        name=name,
        runtime=runtime,
        entrypoint=entrypoint,
        surface=surface,
        owns=owns,
        compatibility_aliases=list(compatibility_aliases or []),
    ).as_dict()


@runtime_checkable
class RuntimeBoundaryProtocol(Protocol):
    """Minimal shared surface for runtime boundary discovery.

    Product ``AgentRuntime`` and core ``SingleAgentRuntime`` intentionally keep
    different execution methods. This protocol only fixes the common inspection
    surface that DI, readiness gates, and tests can depend on safely.
    """

    def boundary(self) -> Dict[str, Any]: ...

    def config(self) -> Dict[str, Any]: ...


def run_record_contract(run: Dict[str, Any], *, runtime: str = "multi_agent") -> Dict[str, Any]:
    """Build the family contract from a persisted agent run row."""
    run = dict(run or {})
    timeline = list(run.get("timeline") or [])
    retry_history = list(run.get("retry_history") or [])
    roles = list(run.get("roles_run") or run.get("requested_roles") or [])
    if not roles:
        roles = [
            str(item.get("role"))
            for item in timeline
            if isinstance(item, dict) and item.get("role")
        ]
    return AgentRunContract(
        run_id=run.get("id") or run.get("run_id"),
        agent_id=str(run.get("agent_id") or "agent:executor"),
        runtime=runtime,
        mode=str(run.get("mode") or "simulation"),
        status=str(run.get("status") or "unknown"),
        goal=str(run.get("input") or run.get("goal") or ""),
        roles=roles,
        current_role=run.get("current_role"),
        retries=len(retry_history),
        timeline=timeline,
        artifacts=[
            {
                "type": "run_record",
                "workspace_id": run.get("workspace_id"),
                "graph_node_id": run.get("graph_node_id"),
                "execution_mode": run.get("execution_mode"),
            }
        ],
        blocking_reasons=[str(run.get("error"))] if run.get("error") else [],
    ).as_dict()


def single_agent_contract(
    *,
    ctx: Any,
    goal: str,
    run_id: Optional[str] = None,
    agent_id: str = "agent:single",
    mode: str = "llm",
    blocking_reasons: Optional[List[str]] = None,
) -> Dict[str, Any]:
    state = getattr(getattr(ctx, "state", None), "value", getattr(ctx, "state", "unknown"))
    status = "done" if state == "DONE" else "failed" if state == "FAILED" else str(state or "unknown").lower()
    return AgentRunContract(
        run_id=run_id,
        agent_id=agent_id,
        runtime="single_agent",
        mode=mode,
        status=status,
        goal=goal,
        roles=["planner", "executor", "critic"],
        current_role=str(state or ""),
        retries=int(getattr(ctx, "retry_count", 0) or 0),
        timeline=list(getattr(ctx, "transcript", []) or []),
        artifacts=[],
        blocking_reasons=list(blocking_reasons or []),
    ).as_dict()


# ── Sibling-surface contract builders ───────────────────────────────────────
# These wrap the other three observability surfaces into the same family so a
# consumer can iterate agent runs, workflow runs, audit events and realtime
# events through one envelope.

def workflow_run_contract(run: Any) -> Dict[str, Any]:
    """Wrap a workflow run dict (``WorkflowRun.as_dict()``) in the family envelope.

    Accepts either a ``WorkflowRun`` instance or its serialized dict so the
    engine can stamp without importing the dataclass here (avoids a cycle).
    """
    body = run.as_dict() if hasattr(run, "as_dict") else dict(run or {})
    run_id = body.get("id") or body.get("run_id") or body.get("workflow_id")
    workflow_id = body.get("workflow_id")
    contract_body = {
        "run_id": run_id,
        "agent_id": f"workflow:{workflow_id or body.get('name') or 'workflow'}",
        "runtime": "workflow",
        "mode": body.get("mode") or "live",
        "goal": body.get("name") or "workflow",
        "roles": ["workflow"],
        "current_role": body.get("current_node") or body.get("paused_node"),
        "retries": 0,
        "timeline": list(body.get("timeline") or []),
        "artifacts": [
            {
                "type": "workflow_outputs",
                "workflow_id": workflow_id,
                "outputs": body.get("outputs") or {},
                "pause": body.get("pause") or body.get("pending_approval"),
            }
        ],
        "blocking_reasons": [str((body.get("outputs") or {}).get("error"))] if (body.get("outputs") or {}).get("error") else [],
        "is_terminal": str(body.get("status") or "") in {"ok", "failed", "cancelled", "interrupted", "partial", "rejected"},
    }
    return stamp_contract(
        contract_body,
        kind="workflow_run",
        identity=run_id,
        status=str(body.get("status") or "unknown"),
        timestamp=body.get("started_at") or body.get("created_at"),
    )


def audit_event_contract(event: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a single audit-log event in the family envelope.

    Audit events have no lifecycle ``status``; we expose the event type as the
    status so the envelope stays uniform and filterable. Identity is a stable
    ``event_type@timestamp`` key (audit events are append-only, not addressable).
    """
    body = dict(event or {})
    event_type = str(body.get("event_type") or "event")
    ts = body.get("timestamp")
    identity = body.get("event_id") or (f"{event_type}@{ts}" if ts else event_type)
    contract_body = {
        "run_id": body.get("run_id"),
        "agent_id": str(body.get("agent_id") or body.get("workflow_id") or f"audit:{event_type}"),
        "runtime": "audit",
        "mode": "event",
        "goal": event_type,
        "roles": [],
        "current_role": None,
        "retries": int(body.get("retries") or 0),
        "timeline": [{"event": event_type, "timestamp": ts, "status": body.get("status") or event_type}],
        "artifacts": [{"type": "audit_payload", "payload": body}],
        "blocking_reasons": [],
        "is_terminal": True,
    }
    return stamp_contract(
        contract_body,
        kind="audit_event",
        identity=identity,
        status=event_type,
        timestamp=ts,
    )


def realtime_event_contract(event: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a realtime bus event in the family envelope.

    The bus already assigns a monotonic ``seq`` and ``received_at``; we reuse
    those as identity and timestamp so the envelope is consistent with the
    feed's own ordering guarantees.
    """
    body = dict(event or {})
    seq = body.get("seq")
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else {}
    contract_body = {
        "run_id": payload.get("run_id") or body.get("run_id"),
        "agent_id": str(payload.get("agent_id") or payload.get("workflow_id") or f"realtime:{body.get('area') or 'workspace'}"),
        "runtime": "realtime",
        "mode": "event",
        "goal": str(body.get("event_type") or "event"),
        "roles": [],
        "current_role": payload.get("current_role"),
        "retries": int(payload.get("retries") or 0),
        "timeline": [{"event": body.get("event_type") or "event", "timestamp": body.get("received_at"), "payload": payload}],
        "artifacts": [{"type": "realtime_payload", "payload": payload}],
        "blocking_reasons": [str(payload.get("error"))] if payload.get("error") else [],
        "is_terminal": str(payload.get("status") or "") in {"ok", "failed", "cancelled", "interrupted", "partial", "rejected"},
    }
    return stamp_contract(
        contract_body,
        kind="realtime_event",
        identity=f"rt:{seq}" if seq is not None else None,
        status=str(body.get("event_type") or "event"),
        timestamp=body.get("received_at"),
    )
