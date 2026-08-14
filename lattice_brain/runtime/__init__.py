"""The tool dispatch lifecycle.

This subsystem held the multi-agent orchestrator, the agent runtime facade and
the persisted hooks registry. Orchestration is ``lattice-agent``'s and the hooks
platform is ``lattice-platform``'s since v11.6.0; what remains is
:func:`~lattice_brain.runtime.hooks.dispatch_tool` and the two context objects a
bound hook runner is handed.
"""

from __future__ import annotations

__all__ = ["HookContext", "HookResult", "dispatch_tool"]


def __getattr__(name: str):
    if name in set(__all__):
        from . import hooks

        return getattr(hooks, name)
    raise AttributeError(name)
