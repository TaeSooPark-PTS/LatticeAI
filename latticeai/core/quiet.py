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
import sys
from typing import Optional

logger = logging.getLogger("latticeai.suppressed")


def quiet(reason: Optional[str] = None, *, level: int = logging.DEBUG) -> None:
    """Record the exception currently being handled, then continue.

    Safe to call outside an ``except`` block (it becomes a no-op) so a refactor
    that moves it cannot itself raise.
    """
    exc_type, exc, tb = sys.exc_info()
    if exc is None:
        return

    where = "<unknown>"
    if tb is not None:
        # The frame that raised is the useful one; walk to the deepest.
        deepest = tb
        while deepest.tb_next is not None:
            deepest = deepest.tb_next
        frame = deepest.tb_frame
        where = f"{frame.f_code.co_filename}:{frame.f_lineno} in {frame.f_code.co_name}"

    label = f" ({reason})" if reason else ""
    if logger.isEnabledFor(level):
        logger.log(
            level,
            "suppressed %s at %s%s: %s",
            getattr(exc_type, "__name__", "Exception"),
            where,
            label,
            exc,
            exc_info=exc,
        )


__all__ = ["quiet"]
