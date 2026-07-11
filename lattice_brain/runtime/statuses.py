"""Canonical lifecycle states shared by persisted agent and workflow runs."""

RUN_ACTIVE_STATUSES = frozenset(
    {"queued", "running", "in_progress", "retrying", "cancelling"}
)
RUN_TERMINAL_STATUSES = frozenset(
    {"ok", "retried_ok", "failed", "rejected", "cancelled", "interrupted", "partial"}
)

__all__ = ["RUN_ACTIVE_STATUSES", "RUN_TERMINAL_STATUSES"]
