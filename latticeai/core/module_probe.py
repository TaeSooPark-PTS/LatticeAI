from __future__ import annotations

import importlib
import importlib.util

__all__ = ["module_available"]


def module_available(name: str, *, strict: bool = False) -> bool:
    try:
        if strict:
            importlib.import_module(name)
            return True
        return importlib.util.find_spec(name) is not None
    except (
        ImportError,
        OSError,
        ValueError,
        TypeError,
        RuntimeError,
        AttributeError,
        SyntaxError,
    ):
        return False
