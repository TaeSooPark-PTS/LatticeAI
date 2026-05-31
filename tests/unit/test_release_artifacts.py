"""Tests for scripts/validate_release_artifacts.py."""

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "validate_release_artifacts.py"
_spec = importlib.util.spec_from_file_location("validate_release_artifacts", _MODULE_PATH)
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)


def _write_python_artifacts(dist: Path, version: str) -> None:
    (dist / f"ltcai-{version}-py3-none-any.whl").write_bytes(b"fake-wheel")
    (dist / f"ltcai-{version}.tar.gz").write_bytes(b"fake-sdist")


def _write_vsix(dist: Path, version: str, *, with_entrypoint: bool = True, internal_version: str = None) -> Path:
    path = dist / f"ltcai-{version}.vsix"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("extension/package.json", json.dumps({"version": internal_version or version}))
        if with_entrypoint:
            zf.writestr("extension/out/extension.js", "console.log('ok')")
        else:
            zf.writestr("extension/readme.md", "no entrypoint")
    return path


def test_valid_artifacts_pass(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_python_artifacts(dist, "1.1.0")
    _write_vsix(dist, "1.1.0")

    result = validator.validate("1.1.0", dist, require_vsix=True, require_tgz=False)

    assert result["ok"] is True
    assert result["errors"] == []
    assert "wheel" in result["found"] and "sdist" in result["found"] and "vsix" in result["found"]


def test_missing_wheel_fails(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "ltcai-1.1.0.tar.gz").write_bytes(b"fake")

    result = validator.validate("1.1.0", dist, require_vsix=False, require_tgz=False)

    assert result["ok"] is False
    assert any("wheel" in err for err in result["errors"])


def test_vsix_without_entrypoint_fails(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_python_artifacts(dist, "1.1.0")
    _write_vsix(dist, "1.1.0", with_entrypoint=False)

    result = validator.validate("1.1.0", dist, require_vsix=True, require_tgz=False)

    assert result["ok"] is False
    assert any("extension/out/extension.js" in err for err in result["errors"])


def test_vsix_internal_version_mismatch_fails(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_python_artifacts(dist, "1.1.0")
    _write_vsix(dist, "1.1.0", internal_version="1.0.1")

    result = validator.validate("1.1.0", dist, require_vsix=True, require_tgz=False)

    assert result["ok"] is False
    assert any("internal version" in err for err in result["errors"])


def test_mixed_versions_warn_about_glob(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_python_artifacts(dist, "1.1.0")
    _write_vsix(dist, "1.1.0")
    # A stale build from a previous release lingering in dist/.
    (dist / "ltcai-1.0.1-py3-none-any.whl").write_bytes(b"stale")

    result = validator.validate("1.1.0", dist, require_vsix=True, require_tgz=False)

    assert result["ok"] is True  # required artifacts are present
    assert any("dist/*" in warning for warning in result["warnings"])


def test_invalid_version_string_fails(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    result = validator.validate("not-a-version", dist, require_vsix=False, require_tgz=False)
    assert result["ok"] is False
