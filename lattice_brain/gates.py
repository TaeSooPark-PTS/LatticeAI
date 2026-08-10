"""Opt-in gates that can still be answered at runtime (v11.2.0).

Every opt-in feature in this product used to decide once, in a constructor:
``self._on = os.getenv("LATTICEAI_…") in {"1", …}``. That is correct for a
process that reads its environment at boot and never changes its mind, and it
is a dead end for the settings screen that is coming — a UI toggle cannot move
a boolean that was already copied into ``self``.

:class:`FeatureGate` is the seam that keeps both true at once. It answers at
*call* time in a fixed order:

1. a **bound resolver** — a callable the app layer supplies (the future
   settings surface, a per-workspace policy, a test double);
2. an explicit **override** set through :meth:`FeatureGate.set`;
3. the **environment variable**, parsed exactly the way the hand-written
   ``os.getenv`` checks it replaces did;
4. the declared **default**.

An untouched gate therefore behaves identically to the frozen read it replaced
— same env var, same truthy words, same default — while a bound resolver wins
without a single change at any construction site. Brain Core owns this because
Brain Core owns the gates that matter most (multi-modal routing, sharing), and
it may not import ``latticeai``.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, Optional

#: The words this product has always accepted for "on" / "off".
TRUTHY = frozenset({"1", "true", "yes", "on"})
FALSY = frozenset({"0", "false", "no", "off"})


class FeatureGate:
    """One opt-in switch, resolved when it is asked rather than when it is built."""

    __slots__ = ("env_var", "default", "name", "detail", "_override", "_resolver")

    def __init__(
        self,
        env_var: str,
        *,
        default: bool = False,
        name: str = "",
        detail: str = "",
    ) -> None:
        self.env_var = env_var
        self.default = bool(default)
        self.name = name or env_var
        #: Plain sentence for a surface that has to explain why a feature is off.
        self.detail = detail
        self._override: Optional[bool] = None
        self._resolver: Optional[Callable[[], bool]] = None

    # ── resolution ───────────────────────────────────────────────────────────
    def __call__(self) -> bool:
        return self.enabled()

    def enabled(self) -> bool:
        """The gate's answer *now* (resolver → override → env → default)."""
        if self._resolver is not None:
            return bool(self._resolver())
        return self.local()

    def local(self) -> bool:
        """This gate's own answer, ignoring any bound resolver.

        The lower three layers on their own (override → env → default). A
        resolver that only has an opinion *sometimes* — a settings service that
        speaks for the features a person actually touched, and stays quiet about
        the rest — hands the question back here, so an operator's environment
        variable keeps working for everything nobody has decided.
        """
        if self._override is not None:
            return self._override
        return self.from_env()

    def from_env(self) -> bool:
        """The environment's answer alone, ignoring resolver and override."""
        raw = os.getenv(self.env_var, "").strip().lower()
        if raw in TRUTHY:
            return True
        if raw in FALSY:
            return False
        return self.default

    def source(self) -> str:
        """Which of the four layers produced the current answer."""
        if self._resolver is not None:
            return "resolver"
        if self._override is not None:
            return "override"
        if os.getenv(self.env_var, "").strip().lower() in (TRUTHY | FALSY):
            return "env"
        return "default"

    # ── injection ────────────────────────────────────────────────────────────
    def set(self, value: Optional[bool]) -> None:
        """Explicit runtime override. ``None`` hands the answer back to the env."""
        self._override = None if value is None else bool(value)

    def bind(self, resolver: Optional[Callable[[], bool]]) -> None:
        """Delegate the answer to a caller-supplied callable (``None`` unbinds)."""
        self._resolver = resolver

    def reset(self) -> None:
        """Forget both injections — the gate is env-driven again."""
        self._override = None
        self._resolver = None

    def describe(self) -> Dict[str, object]:
        """Honest read for a status surface: state *and* where it came from."""
        return {
            "name": self.name,
            "flag": self.env_var,
            "enabled": self.enabled(),
            "default": self.default,
            "source": self.source(),
            "detail": self.detail,
        }


__all__ = ["FALSY", "TRUTHY", "FeatureGate"]
