"""The rebinding half of the process-wide service singletons.

Four wiring modules create one service each — the permission dial, the network
boundary, the hybrid policy, the feature switchboard — and all four had the
same problem and the same four-line answer to it: a lazy first caller (a tool
dispatch, a feature gate) can ask for the service *before* routers know the
configured data dir or the audit sink, and if the later explicit call were
allowed to be a no-op the service would stay pinned to the fallback path with
its audit events going nowhere.

So explicit arguments rebind rather than being dropped. That rule was written
out four times; it lives here once, because "the second caller silently loses"
is precisely the kind of divergence that produces a second, invisible copy of
the user's data and no failing test.

The singleton *slot* deliberately stays a module global in each wiring module:
the modules are the unit of process state, and the suite resets them by
assigning ``_SHARED = None``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from latticeai.core.config import default_data_dir

ServiceT = TypeVar("ServiceT")

__all__ = ["singleton_data_dir", "rebind_singleton"]


def singleton_data_dir(data_dir: Optional[Path]) -> Path:
    """Where a *first* lazy caller should put the store."""
    return Path(data_dir) if data_dir is not None else default_data_dir()


def rebind_singleton(
    service: ServiceT,
    *,
    data_dir: Optional[Path] = None,
    audit: Optional[Callable[..., None]] = None,
) -> ServiceT:
    """Apply explicit arguments to an already-created singleton, then return it.

    ``None`` means "the caller did not say", never "unset it" — a router that
    mounts without an audit sink must not silence a sink an earlier caller
    already supplied.
    """
    rebindable: Any = service
    if data_dir is not None:
        rebindable.rebind_data_dir(Path(data_dir))
    if audit is not None:
        rebindable.rebind_audit(audit)
    return service
