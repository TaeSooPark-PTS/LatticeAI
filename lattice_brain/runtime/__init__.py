from __future__ import annotations

__all__ = ["HookContext", "HookResult", "dispatch_tool"]


def __getattr__(name: str):
    if name in set(__all__):
        from . import hooks

        return getattr(hooks, name)
    raise AttributeError(name)
