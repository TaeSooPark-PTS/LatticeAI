from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional

_SANDBOX_HOME: Optional[str] = None
_SANDBOX_MARKER = "LATTICEAI_TEST_SANDBOX_HOME"


def pytest_configure(config) -> None:
    global _SANDBOX_HOME
    os.environ["LATTICEAI_RERANKER"] = "off"
    if os.environ.get(_SANDBOX_MARKER):
        return
    _SANDBOX_HOME = tempfile.mkdtemp(prefix="lattice-test-home-")
    os.environ[_SANDBOX_MARKER] = _SANDBOX_HOME
    os.environ["HOME"] = _SANDBOX_HOME
    os.environ["USERPROFILE"] = _SANDBOX_HOME


def pytest_unconfigure(config) -> None:
    global _SANDBOX_HOME
    if _SANDBOX_HOME is None:
        return
    shutil.rmtree(_SANDBOX_HOME, ignore_errors=True)
    os.environ.pop(_SANDBOX_MARKER, None)
    _SANDBOX_HOME = None
