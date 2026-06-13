"""Agent and hook runtime subsystem of the Brain Core.

Physically hosts the hooks registry/dispatch lifecycle, the multi-agent
orchestrator, and the agent runtime service. Lazy-loaded so importing
``lattice_brain.runtime`` stays cheap.
"""

from __future__ import annotations

__all__ = [
    "AgentRuntime",
    "AgentRuntimeUnavailable",
    "MultiAgentOrchestrator",
    "HooksRegistry",
    "dispatch_tool",
]


def __getattr__(name: str):
    if name in {"AgentRuntime", "AgentRuntimeUnavailable"}:
        from . import agent_runtime

        return getattr(agent_runtime, name)
    if name == "MultiAgentOrchestrator":
        from .multi_agent import MultiAgentOrchestrator

        return MultiAgentOrchestrator
    if name in {"HooksRegistry", "dispatch_tool"}:
        from . import hooks

        return getattr(hooks, name)
    raise AttributeError(name)
