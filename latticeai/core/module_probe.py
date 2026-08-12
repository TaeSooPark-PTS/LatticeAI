"""One answer to "is this module importable?".

Four modules asked it four different ways: two consulted ``find_spec``, one
imported for real, and they disagreed about which failures mean "no" and which
should escape. The disagreement mattered — the setup probe's copy imports for
real *on purpose*, because a native wheel like ``mlx`` can have a perfectly
findable spec on the wrong architecture and still raise when it loads. A copy
that only looked for the spec would report the runtime as present and let the
recommendation engine plan an install around a module that cannot run.

So the two behaviours stay, named: ``strict=False`` asks whether Python can
*find* the module (cheap, no side effects, no import cost); ``strict=True``
actually imports it and answers whether it *works*.
"""

from __future__ import annotations

import importlib
import importlib.util

__all__ = ["module_available"]


def module_available(name: str, *, strict: bool = False) -> bool:
    """Report whether ``name`` can be imported, never raising.

    A probe that raises is a probe that takes the caller down for asking, and
    every caller here is deciding what to *offer* the user — "I could not tell"
    and "it is not there" lead to the same honest answer.
    """
    try:
        if strict:
            importlib.import_module(name)
            return True
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False
