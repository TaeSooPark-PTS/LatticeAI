"""wpb01 branch coverage — ``lattice_brain.archive`` (.latticebrain archives).

Three groups:

* the two swap helpers' rollback guards, exercised in the state where there is
  nothing left to undo (the temp copy or the staged tree already vanished, or
  no pre-restore backup was ever taken) — the original failure must still be
  the exception the caller sees;
* payload enumeration skipping directories and a data dir with no
  ``workspace_exports/``;
* ``inspect`` without a passphrase, and ``restore`` into a target that has no
  blob dir / no data dir, plus a payload whose ``data/`` and
  ``workspace_exports/`` sections are nested rather than flat.

The KDF iteration count is lowered for the whole module: it is a cost knob,
not a code path, and 390k PBKDF2 rounds per archive dominate the runtime.
"""

from __future__ import annotations

import base64
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Dict

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.archive as archive_mod  # noqa: E402
from lattice_brain.archive import (  # noqa: E402
    BrainArchivePaths,
    EncryptedBrainArchive,
    _replace_sqlite_atomically,
    _replace_tree_with_backup,
)

PASSPHRASE = "wpb01-passphrase"


@pytest.fixture(autouse=True)
def _fast_kdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive_mod, "KDF_ITERATIONS", 1_000)


def _fail_swap(monkeypatch: pytest.MonkeyPatch, dest: Path, *, drop_staged: bool) -> None:
    import os
    import shutil

    real_replace = os.replace

    def fake_replace(src, dst, *args, **kwargs):
        if Path(dst) == dest:
            if drop_staged:
                shutil.rmtree(src)
            raise RuntimeError("swap failed")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fake_replace)


def _seed_brain(tmp_path: Path) -> BrainArchivePaths:
    """A minimal on-disk brain: sqlite file + one blob + portable data file."""
    db_path = tmp_path / "brain" / "knowledge_graph.sqlite"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"sqlite-bytes")
    blob_dir = tmp_path / "brain" / "blobs"
    blob_dir.mkdir()
    (blob_dir / "b.bin").write_bytes(b"blob")
    data_dir = tmp_path / "brain" / "data"
    data_dir.mkdir()
    (data_dir / "users.json").write_text("{}", encoding="utf-8")
    return BrainArchivePaths(db_path=db_path, blob_dir=blob_dir, data_dir=data_dir)


def _write_crafted_archive(dest: Path, members: Dict[str, bytes]) -> Path:
    """Build a valid encrypted archive around arbitrary payload members."""
    buffer = io.BytesIO()
    entries = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
            entries.append(
                {
                    "path": name,
                    "bytes": len(data),
                    "sha256": archive_mod._sha256_bytes(data),
                }
            )
        manifest = {
            "format": "latticebrain.payload",
            "format_version": archive_mod.ARCHIVE_VERSION,
            "created_at": "2026-08-01T00:00:00Z",
            "sections": {
                "graph": True,
                "blobs": False,
                "workspace_state": True,
                "signed_bundles": True,
            },
            "metadata": {},
            "storage": {},
            "device_identity": {},
            "provenance": {},
            "entries": entries,
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    payload = buffer.getvalue()
    salt = b"wpb01-salt-16byt"
    nonce = b"wpb01-nonce1"
    ciphertext = AESGCM(archive_mod._derive_key(PASSPHRASE, salt)).encrypt(
        nonce, payload, None
    )
    envelope = {
        "format": archive_mod.ARCHIVE_FORMAT,
        "format_version": archive_mod.ARCHIVE_VERSION,
        "created_at": "2026-08-01T00:00:00Z",
        "kdf": {
            "name": "PBKDF2HMAC-SHA256",
            "iterations": archive_mod.KDF_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": {
            "name": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode("ascii"),
        },
        "payload_sha256": archive_mod._sha256_bytes(payload),
        "manifest_summary": {
            "format_version": manifest["format_version"],
            "created_at": manifest["created_at"],
            "sections": manifest["sections"],
            "storage": {},
            "device_identity": {},
        },
        "payload": base64.b64encode(ciphertext).decode("ascii"),
    }
    dest.write_text(json.dumps(envelope), encoding="utf-8")
    return dest


# ── rollback guards ─────────────────────────────────────────────────────────


def test_sqlite_rollback_tolerates_a_temp_copy_that_is_already_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure removed the staging file; recovery must not trip over it."""
    src = tmp_path / "incoming.sqlite"
    src.write_bytes(b"incoming")
    dest = tmp_path / "live.sqlite"
    dest.write_bytes(b"live")
    backup_dir = tmp_path / "pre-restore"
    backup_dir.mkdir()
    (backup_dir / dest.name).write_bytes(b"live")

    def boom(path: Path) -> None:
        for leftover in Path(dest).parent.glob("." + dest.name + ".restore-*.tmp"):
            leftover.unlink()
        raise RuntimeError("checkpoint failed")

    monkeypatch.setattr(archive_mod, "_checkpoint_sqlite", boom)

    with pytest.raises(RuntimeError, match="checkpoint failed"):
        _replace_sqlite_atomically(src, dest, backup_dir)

    # Original database recovered from the pre-restore backup, no temp left.
    assert dest.read_bytes() == b"live"
    assert list(tmp_path.glob(".*.restore-*.tmp")) == []


def test_tree_rollback_tolerates_a_staged_tree_that_is_already_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "new.txt").write_text("new", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "old.txt").write_text("old", encoding="utf-8")
    backup_dir = tmp_path / "pre-restore"
    backup_dir.mkdir()
    _fail_swap(monkeypatch, dest, drop_staged=True)

    with pytest.raises(RuntimeError, match="swap failed"):
        _replace_tree_with_backup(src, dest, backup_dir)

    assert (dest / "old.txt").read_text(encoding="utf-8") == "old"


def test_tree_rollback_with_no_backup_reraises_the_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing was at the destination, so no backup exists to copy back."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "new.txt").write_text("new", encoding="utf-8")
    dest = tmp_path / "dest"
    backup_dir = tmp_path / "pre-restore"
    backup_dir.mkdir()
    _fail_swap(monkeypatch, dest, drop_staged=False)

    with pytest.raises(RuntimeError, match="swap failed"):
        _replace_tree_with_backup(src, dest, backup_dir)

    assert not dest.exists()
    assert list(backup_dir.iterdir()) == []


# ── payload enumeration ─────────────────────────────────────────────────────


def test_payload_enumeration_skips_blob_subdirectories(tmp_path: Path) -> None:
    paths = _seed_brain(tmp_path)
    (paths.blob_dir / "nested").mkdir()
    (paths.blob_dir / "nested" / "deep.bin").write_bytes(b"deep")

    names = [arcname for _, arcname in EncryptedBrainArchive(paths)._iter_payload_files()]

    assert names == [
        "knowledge_graph.sqlite",
        "blobs/b.bin",
        "blobs/nested/deep.bin",
        "data/users.json",
    ]


def test_payload_enumeration_stops_when_there_are_no_workspace_exports(
    tmp_path: Path,
) -> None:
    paths = _seed_brain(tmp_path)
    assert not (Path(paths.data_dir) / "workspace_exports").exists()

    names = [arcname for _, arcname in EncryptedBrainArchive(paths)._iter_payload_files()]

    assert names == ["knowledge_graph.sqlite", "blobs/b.bin", "data/users.json"]


# ── inspect / restore ───────────────────────────────────────────────────────


def test_inspect_without_a_passphrase_reports_only_public_envelope_fields(
    tmp_path: Path,
) -> None:
    paths = _seed_brain(tmp_path)
    archive = EncryptedBrainArchive(paths)
    source = Path(archive.create(tmp_path / "brain.latticebrain", passphrase=PASSPHRASE)["path"])

    summary = archive.inspect(source)

    assert summary["valid_envelope"] is True
    assert summary["cipher"] == "AES-256-GCM"
    # No passphrase means no decryption was attempted.
    assert "verified" not in summary
    assert "manifest" not in summary


def test_restore_into_a_database_only_target_skips_blobs_and_data(
    tmp_path: Path,
) -> None:
    paths = _seed_brain(tmp_path)
    archive = EncryptedBrainArchive(paths)
    source = Path(archive.create(tmp_path / "brain.latticebrain", passphrase=PASSPHRASE)["path"])
    target = BrainArchivePaths(db_path=tmp_path / "restored" / "kg.sqlite")

    result = archive.restore(
        source, passphrase=PASSPHRASE, target=target, confirm=True
    )

    assert result["restored"] is True
    assert Path(target.db_path).read_bytes() == b"sqlite-bytes"
    # Neither section was materialised: the target declared no blob/data dir.
    siblings = {p.name for p in Path(target.db_path).parent.iterdir()}
    assert "blobs" not in siblings
    assert "data" not in siblings
    assert Path(result["pre_restore_backup"]).is_dir()


def test_restore_copies_only_regular_files_from_nested_payload_sections(
    tmp_path: Path,
) -> None:
    """``data/`` and ``workspace_exports/`` walks skip the directories they see."""
    source = _write_crafted_archive(
        tmp_path / "crafted.latticebrain",
        {
            "knowledge_graph.sqlite": b"sqlite-bytes",
            "data/users.json": b"{\"u\": 1}",
            "data/nested/users.json": b"{\"ignored\": true}",
            "workspace_exports/nested/bundle.json": b"{\"kept\": true}",
        },
    )
    target = BrainArchivePaths(
        db_path=tmp_path / "restored" / "kg.sqlite",
        data_dir=tmp_path / "restored" / "data",
    )

    result = EncryptedBrainArchive(BrainArchivePaths(db_path=tmp_path / "unused")).restore(
        source, passphrase=PASSPHRASE, target=target, confirm=True
    )

    assert result["restored"] is True
    data_dir = Path(target.data_dir)
    # Flat portable file restored; the nested one is not on the portable list.
    assert (data_dir / "users.json").read_bytes() == b"{\"u\": 1}"
    assert not (data_dir / "nested").exists()
    # Export bundles keep their nested layout.
    assert (data_dir / "workspace_exports" / "nested" / "bundle.json").read_bytes() == (
        b"{\"kept\": true}"
    )
