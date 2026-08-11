"""Shared split shim and probe doubles for the wp04 wizard suites.

``latticeai/setup/wizard.py`` became a package (paths / detect / plans /
catalog / recommend / install). Reading a name through the package still
works, so the test calls are unchanged — but *patching* a name on the package
``__init__`` does not reach a submodule's own global. Every stub is therefore
installed on every module that binds the name, which is exactly the one
binding the single-file module used to have.

This module holds nothing that pytest collects; it is imported by
``test_cov_wp04_wizard_helpers.py`` and ``test_cov_wp04_wizard_detect.py``
so each double has exactly one definition.
"""

import builtins
import io
from pathlib import Path

from latticeai.setup import wizard as setup
from latticeai.setup.wizard import catalog as wizard_catalog
from latticeai.setup.wizard import detect as wizard_detect
from latticeai.setup.wizard import install as wizard_install
from latticeai.setup.wizard import paths as wizard_paths
from latticeai.setup.wizard import plans as wizard_plans
from latticeai.setup.wizard import recommend as wizard_recommend

# ── v11.3.0 split shim ────────────────────────────────────────────────────────
# ``latticeai/setup/wizard.py`` became a package (paths / detect / plans /
# catalog / recommend / install). Reading a name through the package still
# works, so the calls below are unchanged — but *patching* a name on the
# package ``__init__`` does not reach a submodule's own global. Every stub is
# therefore installed on every module that binds the name, which is exactly
# the one binding the single-file module used to have.
_WIZARD_MODULES = (
    setup,
    wizard_catalog,
    wizard_detect,
    wizard_install,
    wizard_paths,
    wizard_plans,
    wizard_recommend,
)


def _patch(monkeypatch, name, value):
    targets = [module for module in _WIZARD_MODULES if hasattr(module, name)]
    assert targets, f"no wizard module binds {name!r}"
    for module in targets:
        monkeypatch.setattr(module, name, value)


class _ModuleShim:
    """Stand-in for an imported module: overrides some names, delegates the rest."""

    def __init__(self, real, **overrides):
        self.__dict__["_real"] = real
        self.__dict__["_overrides"] = overrides

    def __getattr__(self, name):
        overrides = self.__dict__["_overrides"]
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__["_real"], name)


def _fake_cmd(mapping, default=""):
    """Replacement for `setup._cmd` that answers by substring of the argv."""

    def runner(args, timeout=10):
        joined = " ".join(str(part) for part in args)
        for needle, value in mapping.items():
            if needle in joined:
                return value
        return default

    return runner


def _patch_paths(monkeypatch, mapping):
    """Route specific `Path(x).read_text()` calls to canned text or an error."""
    real_path = Path

    class _FakeReadable:
        def __init__(self, payload):
            self._payload = payload

        def read_text(self, *args, **kwargs):
            if isinstance(self._payload, Exception):
                raise self._payload
            return self._payload

    def factory(first, *rest):
        if not rest and str(first) in mapping:
            return _FakeReadable(mapping[str(first)])
        return real_path(first, *rest)

    factory.home = real_path.home
    _patch(monkeypatch, "Path", factory)


def _patch_proc_meminfo(monkeypatch, payload):
    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        if str(file) == "/proc/meminfo":
            if isinstance(payload, Exception):
                raise payload
            return io.StringIO(payload)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
