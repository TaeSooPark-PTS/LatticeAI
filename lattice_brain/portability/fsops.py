"""Atomic file swaps for restore, and the rollback that never masks a failure.

A restore replaces a live SQLite database and a blob tree in place. Each helper
here stages the new copy, keeps a pre-restore backup of what it is about to
overwrite, and — on any failure — puts the old files back *best-effort* while
always re-raising the original error. Recovery I/O that raises would hide the
failure being reported, which is the bug these guards exist for.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Optional

from ..quiet import quiet
from .constants import _stamp


def _safe_zip_names(names) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Backup archive contains unsafe path: {name}")


def _pre_restore_backup_dir(anchor: Path) -> Path:
    backup_dir = anchor.parent / f"{anchor.name}.pre-restore-{_stamp()}"
    index = 1
    while backup_dir.exists():
        backup_dir = anchor.parent / f"{anchor.name}.pre-restore-{_stamp()}-{index}"
        index += 1
    backup_dir.mkdir(parents=True)
    return backup_dir


def _sqlite_siblings(db_path: Path) -> tuple[Path, Path, Path]:
    return (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm"))


def _checkpoint_sqlite(db_path: Path) -> None:
    if not db_path.exists():
        return
    try:
        with closing(sqlite3.connect(str(db_path))) as conn, conn:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
    except sqlite3.Error:
        # Best-effort only. Existing sibling backup/restore still preserves
        # the WAL files if a live connection prevents a checkpoint.
        return


def _restore_sibling(path: Path, backup: Path) -> None:
    try:
        shutil.copy2(backup, path)
    except FileNotFoundError:
        # The backup vanished (or never existed — transient -wal/-shm): the
        # honest reconstruction is "no such sibling", never a crash that
        # masks the error that triggered the rollback.
        path.unlink(missing_ok=True)


def _replace_sqlite_atomically(src: Path, dest: Path, backup_dir: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}.restore-{_stamp()}-{os.getpid()}.tmp"
    shutil.copyfile(src, tmp)
    backups: dict[Path, Path] = {}
    try:
        _checkpoint_sqlite(dest)
        # -wal/-shm are transient: another live connection can checkpoint and
        # remove them between exists() and the copy/unlink. Treat a vanished
        # sibling as "nothing to preserve" instead of crashing the restore.
        for sibling in _sqlite_siblings(dest):
            backup = backup_dir / sibling.name
            try:
                shutil.copy2(sibling, backup)
            except FileNotFoundError:
                quiet()
                continue
            backups[sibling] = backup
        for sibling in _sqlite_siblings(dest)[1:]:
            sibling.unlink(missing_ok=True)
        os.replace(tmp, dest)
    except Exception:
        # Recovery I/O must never replace the swap error being reported —
        # an exception raised here would mask it (the CI-observed
        # "[Errno 2]" over the real failure). Best-effort restore, then
        # always re-raise the original.
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logging.warning("restore tmp cleanup failed: %s", cleanup_exc)
        for sibling in _sqlite_siblings(dest):
            try:
                _restore_sibling(sibling, backups.get(sibling, backup_dir / sibling.name))
            except OSError as rollback_exc:
                logging.warning(
                    "restore sibling rollback incomplete for %s: %s",
                    sibling, rollback_exc,
                )
        raise


def _rollback_sqlite_from_backup(dest: Path, backup_dir: Path) -> None:
    for sibling in _sqlite_siblings(dest):
        _restore_sibling(sibling, backup_dir / sibling.name)


def _replace_tree_with_backup(src: Optional[Path], dest: Path, backup_dir: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = dest.parent / f".{dest.name}.restore-{_stamp()}-{os.getpid()}"
    backup = backup_dir / dest.name
    if src and src.exists():
        shutil.copytree(src, staged)
    else:
        staged.mkdir(parents=True)
    try:
        if dest.exists():
            shutil.copytree(dest, backup)
            shutil.rmtree(dest)
        os.replace(staged, dest)
    except Exception:
        # Same masking guard as the sqlite swap: rollback I/O is
        # best-effort and the original failure always propagates.
        try:
            if staged.exists():
                shutil.rmtree(staged)
            if dest.exists():
                shutil.rmtree(dest)
            if backup.exists():
                shutil.copytree(backup, dest)
        except OSError as rollback_exc:
            logging.warning("blob tree rollback incomplete: %s", rollback_exc)
        raise
