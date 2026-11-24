"""Deliberately-ignored exceptions, recorded instead of erased.

A local-first app that talks to optional hardware, optional models, optional
network paths and user files legitimately swallows a lot. ``except Exception:
pass`` is often the right *behaviour* — a failed probe should not take the
server down. What is not right is that the failure leaves no trace at all: a
genuine bug in one of those paths is indistinguishable from the optional thing
simply being absent, and nobody ever finds out.

``quiet()`` keeps the behaviour and removes the silence. It logs the live
exception at DEBUG with the file, function and line that suppressed it, so the
default experience is unchanged and ``LATTICEAI_LOG_LEVEL=DEBUG`` turns every
swallowed failure into a line you can read.

    try:
        probe_optional_thing()
    except Exception:
        quiet("optional GPU probe")

The label is optional; without one the call site is still identified. Pass one
when the location alone would not tell a reader *why* failure is acceptable.
"""

from __future__ import annotations

import logging
from typing import Optional

from lattice_brain.quiet import record_suppressed

logger = logging.getLogger("latticeai.suppressed")


def quiet(reason: Optional[str] = None, *, level: int = logging.DEBUG) -> None:
    """Record the exception currently being handled, then continue.

    Safe to call outside an ``except`` block (it becomes a no-op) so a refactor
    that moves it cannot itself raise.
    """
    record_suppressed(logger, reason, level=level)


__all__ = ["quiet"]
