"""Suite-wide isolation: the tests never touch the developer's real HOME.

This exists because they did. Several product modules resolve their storage the
way a local-first product should — ``Path.home() / ".ltcai"`` for the data
directory, ``Path.home() / ".ltcai-brain"`` for the mirror vault — and a few of
them do it at *import* time (``latticeai.integrations.telegram_bot`` creates the
data directory as a module side effect). A test that imports such a module has
already written to the real home before its first ``monkeypatch`` line runs, so
per-test seams could not close this: by then the constant is bound and the
directory exists.

The fix is to move ``HOME`` before collection starts. ``pytest_configure`` runs
before any test module is imported, so every ``Path.home()`` in the tree — at
import time or later — resolves inside a temporary directory that is removed
when the session ends.

Deliberately **only** ``HOME``/``USERPROFILE``:

* it is the single root every one of those resolvers falls back to, so one
  variable fixes all of them at once;
* it changes no configured behaviour. ``LATTICEAI_DATA_DIR`` and friends still
  win where they are set, so a test asserting "the data dir is
  ``Path.home()/.ltcai`` when nothing is configured" keeps passing — it just
  passes against a sandbox home;
* the ~20 tests that already set ``HOME`` themselves keep overriding it, per
  test, exactly as before.

Anything already exported by the caller (CI, a wrapper script that built its own
sandbox) is respected: this only fills in a home when the process does not
already have one it chose.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional

#: Set when *this* module created the sandbox, so teardown never removes a
#: directory someone else owns.
_SANDBOX_HOME: Optional[str] = None
#: Marks a home this conftest installed, so a nested pytest run (the subprocess
#: tests) can tell "already isolated" from "the developer's real home".
_SANDBOX_MARKER = "LATTICEAI_TEST_SANDBOX_HOME"


def pytest_configure(config) -> None:  # noqa: ARG001 — pytest hook signature
    """Point HOME at a throwaway directory before any test module is imported."""
    global _SANDBOX_HOME
    if os.environ.get(_SANDBOX_MARKER):
        # A parent process (or a harness like scripts/run_integration_tests.mjs)
        # already isolated this run; nesting a second sandbox would only hide
        # which one the writes landed in.
        return
    _SANDBOX_HOME = tempfile.mkdtemp(prefix="lattice-test-home-")
    os.environ[_SANDBOX_MARKER] = _SANDBOX_HOME
    os.environ["HOME"] = _SANDBOX_HOME
    # Windows resolves Path.home() from USERPROFILE; set both so the sandbox is
    # the same directory on every platform this suite runs on.
    os.environ["USERPROFILE"] = _SANDBOX_HOME


def pytest_unconfigure(config) -> None:  # noqa: ARG001 — pytest hook signature
    """Remove the sandbox home, but only the one this session created."""
    global _SANDBOX_HOME
    if _SANDBOX_HOME is None:
        return
    shutil.rmtree(_SANDBOX_HOME, ignore_errors=True)
    os.environ.pop(_SANDBOX_MARKER, None)
    _SANDBOX_HOME = None
