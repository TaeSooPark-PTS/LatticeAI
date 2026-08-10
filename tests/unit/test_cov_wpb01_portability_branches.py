"""wpb01 branch coverage — ``lattice_brain.portability``.

Two seams:

* ``_replace_tree_with_backup`` — the "no destination yet" swap and the two
  rollback guards that must stay silent when there is nothing to undo. The
  contract under test is that the *original* failure always propagates and the
  best-effort recovery never raises a second, masking error.
* ``KGPortabilityService.backup`` — a blob directory that is absent, and one
  that contains a sub-directory (only regular files belong in the archive).
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.portability import (  # noqa: E402
    KGPortabilityService,
    _replace_tree_with_backup,
)


def _fail_swap(monkeypatch: pytest.MonkeyPatch, dest: Path, *, drop_staged: bool) -> None:
    """Make the ``os.replace`` that lands ``dest`` fail, optionally losing staged."""
    real_replace = os.replace

    def fake_replace(src, dst, *args, **kwargs):
        if Path(dst) == dest:
            if drop_staged:
                shutil.rmtree(src)
            raise RuntimeError("swap failed")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fake_replace)


class _StubGraph:
    """Minimal Knowledge Graph surface used by ``backup``."""

    def __init__(self, blob_dir: Path) -> None:
        self.blob_dir = blob_dir

    def schema_versions(self):
        return {"schema_version": 7, "projection_version": 2}

    def backup_database(self, dest) -> None:
        Path(dest).write_bytes(b"sqlite-snapshot")


# ── _replace_tree_with_backup ───────────────────────────────────────────────


def test_tree_swap_into_a_missing_destination_takes_no_backup(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "nested").mkdir(parents=True)
    (src / "nested" / "a.txt").write_text("payload", encoding="utf-8")
    dest = tmp_path / "dest"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    _replace_tree_with_backup(src, dest, backup_dir)

    assert (dest / "nested" / "a.txt").read_text(encoding="utf-8") == "payload"
    # Nothing was there to preserve, so no backup copy was made.
    assert list(backup_dir.iterdir()) == []


def test_tree_rollback_survives_a_staged_dir_that_is_already_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "new.txt").write_text("new", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "old.txt").write_text("old", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _fail_swap(monkeypatch, dest, drop_staged=True)

    with pytest.raises(RuntimeError, match="swap failed"):
        _replace_tree_with_backup(src, dest, backup_dir)

    # The original tree came back from the backup copy.
    assert (dest / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (dest / "new.txt").exists()


def test_tree_rollback_with_nothing_to_restore_reraises_the_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No destination existed, so no backup exists to copy back."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "new.txt").write_text("new", encoding="utf-8")
    dest = tmp_path / "dest"
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    _fail_swap(monkeypatch, dest, drop_staged=False)

    with pytest.raises(RuntimeError, match="swap failed"):
        _replace_tree_with_backup(src, dest, backup_dir)

    assert not dest.exists()
    assert list(backup_dir.iterdir()) == []
    # The staged copy was cleaned up instead of being left behind.
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".dest")] == []


# ── KGPortabilityService.backup ─────────────────────────────────────────────


def test_backup_without_a_blob_directory_reports_has_blobs_false(tmp_path: Path) -> None:
    service = KGPortabilityService(
        knowledge_graph=_StubGraph(tmp_path / "absent-blobs"),
        data_dir=tmp_path / "data",
    )

    result = service.backup(tmp_path / "kg-backup.zip")

    assert result["manifest"]["has_blobs"] is False
    with zipfile.ZipFile(result["path"]) as zf:
        assert sorted(zf.namelist()) == ["knowledge_graph.sqlite", "manifest.json"]


def test_backup_skips_directories_inside_the_blob_store(tmp_path: Path) -> None:
    blob_dir = tmp_path / "blobs"
    (blob_dir / "sub").mkdir(parents=True)
    (blob_dir / "sub" / "kept.bin").write_bytes(b"blob-bytes")
    service = KGPortabilityService(
        knowledge_graph=_StubGraph(blob_dir),
        data_dir=tmp_path / "data",
    )

    result = service.backup(tmp_path / "kg-backup.zip")

    assert result["manifest"]["has_blobs"] is True
    with zipfile.ZipFile(result["path"]) as zf:
        names = sorted(zf.namelist())
    # The "sub" directory itself is not a member; only the file under it is.
    assert names == ["blobs/sub/kept.bin", "knowledge_graph.sqlite", "manifest.json"]
