"""wp19: the slim ``LTCAI`` worker entrypoint.

``main`` loads ``.env``, applies ``LATTICEAI_EXTRA_PATH``, parses
``--host`` / ``--port`` / ``--reload``, writes the address back into the
environment, and hands ``latticeai.worker_app:create_worker_app`` to
uvicorn. ``uvicorn.run`` is replaced through ``sys.modules`` so no test
binds a port or spawns a process.
"""

from __future__ import annotations

import os
import runpy
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, List

import pytest

import latticeai.cli.entrypoint as entrypoint
from latticeai.cli.runtime import _apply_extra_path, _load_env_file


# ── cli.runtime helpers ──────────────────────────────────────────────────────
def test_load_env_file_fills_only_unset_keys_and_skips_noise(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "not-an-assignment",
                'LATTICEAI_WP19_QUOTED="quoted value"',
                "  LATTICEAI_WP19_PLAIN = plain  ",
                "LATTICEAI_WP19_PRESET=from-file",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("LATTICEAI_WP19_QUOTED", raising=False)
    monkeypatch.delenv("LATTICEAI_WP19_PLAIN", raising=False)
    monkeypatch.setenv("LATTICEAI_WP19_PRESET", "from-environment")

    _load_env_file(env_file)

    assert os.environ["LATTICEAI_WP19_QUOTED"] == "quoted value"
    assert os.environ["LATTICEAI_WP19_PLAIN"] == "plain"
    # An existing environment value always wins over the file.
    assert os.environ["LATTICEAI_WP19_PRESET"] == "from-environment"
    monkeypatch.delenv("LATTICEAI_WP19_QUOTED")
    monkeypatch.delenv("LATTICEAI_WP19_PLAIN")


def test_load_env_file_is_a_no_op_when_the_file_is_absent(tmp_path):
    before = dict(os.environ)

    _load_env_file(tmp_path / "does-not-exist.env")

    assert dict(os.environ) == before


def test_apply_extra_path_returns_early_without_the_env_var(monkeypatch):
    monkeypatch.delenv("LATTICEAI_EXTRA_PATH", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin")

    _apply_extra_path()

    assert os.environ["PATH"] == "/usr/bin"


def test_apply_extra_path_prepends_existing_directories_in_order(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", str(second)]))
    monkeypatch.setenv(
        "LATTICEAI_EXTRA_PATH",
        os.pathsep.join([str(first), str(second), str(tmp_path / "absent")]),
    )

    _apply_extra_path()

    # `first` is new so it is prepended; `second` is already on PATH and the
    # missing directory is dropped entirely.
    assert os.environ["PATH"].split(os.pathsep) == [
        str(first),
        "/usr/bin",
        str(second),
    ]


# ── main ─────────────────────────────────────────────────────────────────────
def _stable_process(monkeypatch, tmp_path) -> List[Dict[str, Any]]:
    """Pin cwd, env and uvicorn so ``main`` cannot escape the test."""
    served: List[Dict[str, Any]] = []
    monkeypatch.chdir(Path.cwd())
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.delenv("LATTICEAI_EXTRA_PATH", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("LATTICEAI_HOST", "127.0.0.1")
    monkeypatch.setenv("LATTICEAI_PORT", "4825")
    fake_uvicorn = ModuleType("uvicorn")
    fake_uvicorn.__spec__ = ModuleSpec("uvicorn", loader=None)
    fake_uvicorn.run = lambda app, **kwargs: served.append({"app": app, **kwargs})
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    return served


def test_main_writes_host_and_port_back_into_the_environment(monkeypatch, tmp_path, capsys):
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["LTCAI", "--host", "0.0.0.0", "--port", "4901"])

    entrypoint.main()

    assert os.environ["LATTICEAI_HOST"] == "0.0.0.0"
    assert os.environ["LATTICEAI_PORT"] == "4901"
    assert served == [
        {
            "app": "latticeai.worker_app:create_worker_app",
            "factory": True,
            "host": "0.0.0.0",
            "port": 4901,
            "reload": False,
            "log_level": "info",
        }
    ]
    assert "Lattice AI worker on http://0.0.0.0:4901" in capsys.readouterr().out


def test_main_defaults_come_from_the_environment(monkeypatch, tmp_path):
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setenv("LATTICEAI_HOST", "10.0.0.2")
    monkeypatch.setenv("LATTICEAI_PORT", "4999")
    monkeypatch.setattr(sys, "argv", ["LTCAI"])

    entrypoint.main()

    assert served[0]["host"] == "10.0.0.2"
    assert served[0]["port"] == 4999
    assert served[0]["factory"] is True
    assert served[0]["reload"] is False
    assert os.environ["LATTICEAI_HOST"] == "10.0.0.2"
    assert os.environ["LATTICEAI_PORT"] == "4999"


def test_main_reload_flag_is_passed_to_uvicorn(monkeypatch, tmp_path):
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["LTCAI", "--reload"])

    entrypoint.main()

    assert served[0]["reload"] is True
    assert served[0]["app"] == "latticeai.worker_app:create_worker_app"
    assert served[0]["factory"] is True


def test_main_rejects_an_unknown_flag(monkeypatch, tmp_path):
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["LTCAI", "--tunnel"])

    with pytest.raises(SystemExit) as exited:
        entrypoint.main()

    assert exited.value.code == 2
    assert served == []


@pytest.mark.filterwarnings("ignore:.*found in sys.modules.*:RuntimeWarning")
def test_running_the_module_as_a_script_reaches_main(monkeypatch, tmp_path):
    """``python -m latticeai.cli.entrypoint`` must call ``main``."""
    served = _stable_process(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["LTCAI", "--port", "4826"])

    runpy.run_module("latticeai.cli.entrypoint", run_name="__main__")

    assert served == [
        {
            "app": "latticeai.worker_app:create_worker_app",
            "factory": True,
            "host": "127.0.0.1",
            "port": 4826,
            "reload": False,
            "log_level": "info",
        }
    ]
