"""Compatibility alias for the physical :mod:`latticeai.tools` package.

This shim replaces itself with the implementation module instead of copying
exports. Thus legacy and package imports share globals, registry instances,
submodules, and monkeypatches.
"""

from __future__ import annotations

import importlib
import sys


_IMPLEMENTATION = importlib.import_module("latticeai.tools")

for _submodule in (
    "commands",
    "computer",
    "documents",
    "filesystem",
    "knowledge",
    "local_files",
    "network",
):
    sys.modules[f"{__name__}.{_submodule}"] = importlib.import_module(
        f"latticeai.tools.{_submodule}"
    )

sys.modules[__name__] = _IMPLEMENTATION
