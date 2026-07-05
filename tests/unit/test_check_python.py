"""Regression tests for the side-effect-free Python syntax gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_python.py"
spec = importlib.util.spec_from_file_location("check_python", MODULE_PATH)
check_python = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(check_python)


def test_iter_modules_skips_generated_output_and_cache_dirs(monkeypatch, tmp_path: Path):
    source = tmp_path / "pkg" / "good.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")

    generated = tmp_path / "output" / "generated.py"
    generated.parent.mkdir()
    generated.write_text("BROKEN =\n", encoding="utf-8")

    cache = tmp_path / "pkg" / "__pycache__" / "cached.py"
    cache.parent.mkdir()
    cache.write_text("BROKEN =\n", encoding="utf-8")

    monkeypatch.setattr(check_python, "ROOT", tmp_path)

    assert list(check_python.iter_modules()) == [source]


def test_main_does_not_create_bytecode_cache(monkeypatch, tmp_path: Path):
    source = tmp_path / "pkg" / "good.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")

    monkeypatch.setattr(check_python, "ROOT", tmp_path)

    assert check_python.main([]) == 0
    assert not list(tmp_path.rglob("__pycache__"))
    assert not list(tmp_path.rglob("*.pyc"))


def test_main_reports_syntax_failures(monkeypatch, tmp_path: Path):
    source = tmp_path / "pkg" / "broken.py"
    source.parent.mkdir()
    source.write_text("VALUE =\n", encoding="utf-8")

    monkeypatch.setattr(check_python, "ROOT", tmp_path)

    assert check_python.main([]) == 1
    assert not list(tmp_path.rglob("__pycache__"))
    assert not list(tmp_path.rglob("*.pyc"))
