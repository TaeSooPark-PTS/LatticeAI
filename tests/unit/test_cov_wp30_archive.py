"""wp30 coverage — encrypted ``.latticebrain`` archive edges.

The archive is the user's brain leaving the machine, so every rejection has to
be explicit: no passphrase, no database, an envelope that is not ours, a
payload whose members do not match its own manifest, a member path that tries
to escape the extraction directory. This file drives those refusals plus the
selective parts of the payload walk (which workspace exports travel, which
restored ``data/`` files are honoured) and the swap helpers' rollback.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path
from typing import Dict

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import lattice_brain.archive as archive_mod
from lattice_brain.archive import (
    ARCHIVE_FORMAT,
    ARCHIVE_VERSION,
    BrainArchivePaths,
    EncryptedBrainArchive,
    _assert_safe_member,
    _checkpoint_sqlite,
    _derive_key,
    _pre_restore_backup_dir,
    _replace_sqlite_atomically,
    _replace_tree_with_backup,
    _restore_sibling,
    _safe_json,
    _sha256_bytes,
)

PASSPHRASE = "correct horse battery staple"


@pytest.fixture()
def fast_kdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both derivation sites read the module constant, so this stays consistent."""
    monkeypatch.setattr(archive_mod, "KDF_ITERATIONS", 1)


def _sqlite_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO nodes VALUES ('n1')")
    return path


def _payload_zip(members: Dict[str, bytes], *, manifest_entries=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
        if manifest_entries is not None:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "format": "latticebrain.payload",
                        "format_version": ARCHIVE_VERSION,
                        "created_at": "2026-08-09T00:00:00+00:00",
                        "sections": {},
                        "metadata": {},
                        "entries": manifest_entries,
                    }
                ),
            )
    return buffer.getvalue()


def _entries_for(members: Dict[str, bytes]):
    return [
        {"path": name, "bytes": len(data), "sha256": _sha256_bytes(data)}
        for name, data in members.items()
    ]


def _envelope_bytes(payload: bytes, *, passphrase: str = PASSPHRASE, **overrides) -> str:
    salt = os.urandom(16)
    nonce = os.urandom(12)
    ciphertext = AESGCM(_derive_key(passphrase, salt)).encrypt(nonce, payload, None)
    envelope = {
        "format": ARCHIVE_FORMAT,
        "format_version": ARCHIVE_VERSION,
        "created_at": "2026-08-09T00:00:00+00:00",
        "kdf": {"name": "PBKDF2HMAC-SHA256", "salt": base64.b64encode(salt).decode("ascii")},
        "cipher": {"name": "AES-256-GCM", "nonce": base64.b64encode(nonce).decode("ascii")},
        "payload_sha256": _sha256_bytes(payload),
        "manifest_summary": {},
        "payload": base64.b64encode(ciphertext).decode("ascii"),
    }
    envelope.update(overrides)
    return json.dumps(envelope)


def _archive(tmp_path: Path) -> EncryptedBrainArchive:
    return EncryptedBrainArchive(
        BrainArchivePaths(db_path=_sqlite_file(tmp_path / "brain" / "kg.sqlite"))
    )


# ── module helpers ───────────────────────────────────────────────────────────

def test_derive_key_requires_a_passphrase():
    with pytest.raises(ValueError, match="passphrase is required"):
        _derive_key("", b"salt")


def test_safe_json_degrades_unserializable_values_instead_of_raising():
    class Opaque:
        def __repr__(self):
            return "<opaque>"

    assert _safe_json({"a": 1}) == {"a": 1}
    assert _safe_json({"k": Opaque()}) == {"k": "<opaque>"}
    assert _safe_json([Opaque(), 2]) == ["<opaque>", 2]
    assert _safe_json((Opaque(),)) == ["<opaque>"]
    assert _safe_json(Opaque()) == "<opaque>"


def test_assert_safe_member_blocks_escapes():
    assert _assert_safe_member("blobs/a.bin").parts == ("blobs", "a.bin")
    with pytest.raises(ValueError, match="unsafe path"):
        _assert_safe_member("../escape")
    with pytest.raises(ValueError, match="unsafe path"):
        _assert_safe_member("/etc/passwd")


def test_pre_restore_backup_dir_never_reuses_an_existing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_mod, "_stamp", lambda: "20260809T0000")
    anchor = tmp_path / "kg.sqlite"
    first = _pre_restore_backup_dir(anchor)
    second = _pre_restore_backup_dir(anchor)
    assert first.name.endswith("pre-restore-20260809T0000")
    assert second.name.endswith("pre-restore-20260809T0000-1")


def test_checkpoint_sqlite_skips_absent_and_non_database_files(tmp_path):
    _checkpoint_sqlite(tmp_path / "absent.sqlite")
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"definitely not sqlite")
    _checkpoint_sqlite(broken)
    assert broken.read_bytes() == b"definitely not sqlite"


def test_restore_sibling_removes_the_file_when_no_backup_exists(tmp_path):
    live = tmp_path / "kg.sqlite-wal"
    live.write_bytes(b"wal")
    _restore_sibling(live, tmp_path / "backup" / "kg.sqlite-wal")
    assert not live.exists()


def test_sqlite_swap_preserves_and_clears_wal_siblings(tmp_path):
    src = _sqlite_file(tmp_path / "incoming.sqlite")
    dest = tmp_path / "live" / "kg.sqlite"
    dest.parent.mkdir(parents=True)
    # A second live connection keeps -wal/-shm on disk through the checkpoint,
    # which is exactly the state the swap has to back up and clear.
    live = sqlite3.connect(str(dest))
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("CREATE TABLE nodes (id TEXT)")
        live.execute("INSERT INTO nodes VALUES ('old')")
        live.commit()
        wal = Path(str(dest) + "-wal")
        assert wal.exists()
        backup_dir = tmp_path / "pre-restore"
        backup_dir.mkdir()

        _replace_sqlite_atomically(src, dest, backup_dir)

        assert not wal.exists()  # stale sibling removed with the swapped database
        assert (backup_dir / "kg.sqlite-wal").exists()
        assert (backup_dir / "kg.sqlite").exists()
    finally:
        live.close()
    with sqlite3.connect(str(dest)) as conn:
        assert conn.execute("SELECT id FROM nodes").fetchone()[0] == "n1"


def test_blob_tree_swap_restores_the_backup_when_the_copy_fails(tmp_path):
    src = tmp_path / "incoming"
    src.mkdir()
    (src / "new.bin").write_bytes(b"new")
    dest = tmp_path / "blobs"
    dest.mkdir()
    (dest / "live.bin").write_bytes(b"live")
    backup_dir = tmp_path / "pre-restore"
    # The backup slot already holds a directory, so copytree(dest, backup) fails.
    (backup_dir / "blobs").mkdir(parents=True)
    (backup_dir / "blobs" / "recovered.bin").write_bytes(b"recovered")

    with pytest.raises(FileExistsError):
        _replace_tree_with_backup(src, dest, backup_dir)

    assert (dest / "recovered.bin").read_bytes() == b"recovered"


def test_blob_tree_swap_stages_an_empty_directory_without_a_source(tmp_path):
    dest = tmp_path / "blobs"
    dest.mkdir()
    (dest / "stale.bin").write_bytes(b"stale")
    backup_dir = tmp_path / "pre-restore"
    backup_dir.mkdir()

    _replace_tree_with_backup(None, dest, backup_dir)

    assert list(dest.iterdir()) == []


# ── create ───────────────────────────────────────────────────────────────────

def test_create_requires_an_existing_database(tmp_path, fast_kdf):
    archive = EncryptedBrainArchive(BrainArchivePaths(db_path=tmp_path / "absent.sqlite"))
    with pytest.raises(FileNotFoundError, match="Brain database not found"):
        archive.create(tmp_path / "out.latticebrain", passphrase=PASSPHRASE)


def test_create_forces_the_extension_and_filters_workspace_exports(tmp_path, fast_kdf):
    data_dir = tmp_path / "data"
    exports = data_dir / "workspace_exports"
    (exports / "nested").mkdir(parents=True)
    (exports / "keep.json").write_text("{}", encoding="utf-8")
    (exports / "keep.zip").write_bytes(b"PK")
    (exports / "skip.latticebrain").write_text("{}", encoding="utf-8")
    (exports / "skip.txt").write_text("notes", encoding="utf-8")
    (data_dir / "users.json").write_text("[]", encoding="utf-8")
    (data_dir / "not_portable.json").write_text("{}", encoding="utf-8")
    blob_dir = tmp_path / "blobs"
    blob_dir.mkdir()
    (blob_dir / "b.bin").write_bytes(b"blob")

    archive = EncryptedBrainArchive(
        BrainArchivePaths(
            db_path=_sqlite_file(tmp_path / "brain" / "kg.sqlite"),
            blob_dir=blob_dir,
            data_dir=data_dir,
            metadata={"storage": {"engine": "sqlite"}, "obj": object()},
        )
    )
    made = archive.create(tmp_path / "brain-backup.bin", passphrase=PASSPHRASE)

    assert made["path"].endswith(".latticebrain")
    assert Path(made["path"]).is_file()
    verified = archive.verify(Path(made["path"]), passphrase=PASSPHRASE)
    paths = {entry["path"] for entry in verified["manifest"]["entries"]}
    assert paths == {
        "knowledge_graph.sqlite",
        "blobs/b.bin",
        "data/users.json",
        "workspace_exports/keep.json",
        "workspace_exports/keep.zip",
    }
    assert isinstance(verified["manifest"]["metadata"]["obj"], str)


# ── envelope + payload rejections ────────────────────────────────────────────

def test_load_envelope_rejects_missing_malformed_and_foreign_archives(tmp_path):
    archive = _archive(tmp_path)

    with pytest.raises(FileNotFoundError, match="Brain archive not found"):
        archive.inspect(tmp_path / "absent.latticebrain")

    broken = tmp_path / "broken.latticebrain"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        archive.inspect(broken)

    foreign = tmp_path / "foreign.latticebrain"
    foreign.write_text(json.dumps({"format": "something.else"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Not a .latticebrain encrypted archive"):
        archive.inspect(foreign)

    unversioned = tmp_path / "unversioned.latticebrain"
    unversioned.write_text(
        json.dumps({"format": ARCHIVE_FORMAT, "format_version": 0}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="version is missing or invalid"):
        archive.inspect(unversioned)


def test_decrypt_rejects_envelopes_without_encryption_metadata(tmp_path, fast_kdf):
    archive = _archive(tmp_path)
    source = tmp_path / "no-kdf.latticebrain"
    source.write_text(
        json.dumps({"format": ARCHIVE_FORMAT, "format_version": ARCHIVE_VERSION}),
        encoding="utf-8",
    )
    verdict = archive.verify(source, passphrase=PASSPHRASE)
    assert verdict["ok"] is False
    assert "missing encryption metadata" in verdict["errors"][0]


def test_decrypt_rejects_a_payload_whose_hash_changed(tmp_path, fast_kdf):
    archive = _archive(tmp_path)
    source = tmp_path / "tampered.latticebrain"
    payload = _payload_zip({"knowledge_graph.sqlite": b"db"}, manifest_entries=[])
    source.write_text(_envelope_bytes(payload, payload_sha256="0" * 64), encoding="utf-8")
    verdict = archive.verify(source, passphrase=PASSPHRASE)
    assert verdict["ok"] is False
    assert "integrity check failed" in verdict["errors"][0]


def test_read_payload_rejects_corrupt_and_unversioned_payloads(tmp_path):
    archive = _archive(tmp_path)

    with pytest.raises(ValueError, match="not a valid ZIP file"):
        archive._read_payload(b"definitely not a zip")

    # A stored member with a flipped byte fails its own CRC.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("knowledge_graph.sqlite", b"AAAAAAAAAAAA")
    corrupt = bytearray(buffer.getvalue())
    corrupt[corrupt.index(b"AAAAAAAAAAAA")] = ord("B")
    with pytest.raises(ValueError, match="payload member is corrupt"):
        archive._read_payload(bytes(corrupt))

    old = _payload_zip({"knowledge_graph.sqlite": b"db"})
    with pytest.raises(ValueError, match="manifest format version is missing"):
        archive._read_payload(
            _payload_zip(
                {"knowledge_graph.sqlite": b"db", "manifest.json": b'{"format_version": 0}'}
            )
        )
    future = _payload_zip(
        {"knowledge_graph.sqlite": b"db", "manifest.json": b'{"format_version": 99}'}
    )
    with pytest.raises(ValueError, match="newer than supported version"):
        archive._read_payload(future)

    # No manifest at all: one is synthesized from the members themselves.
    manifest, raw = archive._read_payload(old)
    assert manifest["format_version"] == 1
    assert manifest["sections"]["graph"] is True
    assert [entry["path"] for entry in manifest["entries"]] == ["knowledge_graph.sqlite"]
    assert set(raw) == {"knowledge_graph.sqlite"}


def test_verify_reports_members_that_do_not_match_the_manifest(tmp_path, fast_kdf):
    members = {"knowledge_graph.sqlite": b"db-bytes"}
    entries = [
        {"path": "", "bytes": 0, "sha256": "x"},
        {"path": "manifest.json", "bytes": 0, "sha256": "x"},
        {"path": "blobs/absent.bin", "bytes": 3, "sha256": "y"},
        {"path": "knowledge_graph.sqlite", "bytes": 8, "sha256": "0" * 64},
    ]
    source = tmp_path / "mismatch.latticebrain"
    source.write_text(
        _envelope_bytes(_payload_zip(members, manifest_entries=entries)), encoding="utf-8"
    )
    archive = _archive(tmp_path)

    verdict = archive.verify(source, passphrase=PASSPHRASE)
    assert verdict["ok"] is False
    error = verdict["errors"][0]
    assert "missing=['blobs/absent.bin']" in error
    assert "mismatched=['knowledge_graph.sqlite']" in error

    # restore refuses on the same verdict rather than writing a partial brain.
    with pytest.raises(ValueError, match="manifest integrity check failed"):
        archive.restore(
            source,
            passphrase=PASSPHRASE,
            target=BrainArchivePaths(db_path=tmp_path / "target" / "kg.sqlite"),
            confirm=True,
        )


def test_verify_reports_a_payload_without_the_database(tmp_path, fast_kdf):
    members = {"blobs/a.bin": b"blob"}
    source = tmp_path / "no-db.latticebrain"
    source.write_text(
        _envelope_bytes(_payload_zip(members, manifest_entries=_entries_for(members))),
        encoding="utf-8",
    )
    verdict = _archive(tmp_path).verify(source, passphrase=PASSPHRASE)
    assert verdict["ok"] is False
    assert "missing knowledge_graph.sqlite" in verdict["errors"][0]


def test_restore_refuses_when_the_archive_changes_after_verification(
    tmp_path, monkeypatch, fast_kdf
):
    """The payload extracted must be the payload that was verified."""
    good_members = {"knowledge_graph.sqlite": b"db-bytes"}
    good = _envelope_bytes(
        _payload_zip(good_members, manifest_entries=_entries_for(good_members))
    )
    swapped_members = {"blobs/a.bin": b"blob"}
    swapped = _envelope_bytes(
        _payload_zip(swapped_members, manifest_entries=_entries_for(swapped_members))
    )
    source = tmp_path / "swapped.latticebrain"
    source.write_text(good, encoding="utf-8")

    real_read_text = Path.read_text
    reads = {"n": 0}

    def fake_read_text(self, *args, **kwargs):
        if self.name.endswith(".latticebrain"):
            reads["n"] += 1
            # First read (extraction) sees the swapped archive; the
            # verification pass that follows sees the original.
            return swapped if reads["n"] == 1 else good
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    with pytest.raises(ValueError, match="payload is missing knowledge_graph.sqlite"):
        _archive(tmp_path).restore(
            source,
            passphrase=PASSPHRASE,
            target=BrainArchivePaths(db_path=tmp_path / "target" / "kg.sqlite"),
            confirm=True,
        )


def test_restore_only_writes_the_portable_data_files(tmp_path, fast_kdf):
    members = {
        "knowledge_graph.sqlite": _sqlite_file(tmp_path / "seed.sqlite").read_bytes(),
        "blobs/a.bin": b"blob",
        "data/users.json": b"[]",
        "data/rogue.json": b'{"from": "a newer build"}',
        "workspace_exports/export.json": b"{}",
    }
    source = tmp_path / "full.latticebrain"
    source.write_text(
        _envelope_bytes(_payload_zip(members, manifest_entries=_entries_for(members))),
        encoding="utf-8",
    )
    target = BrainArchivePaths(
        db_path=tmp_path / "target" / "kg.sqlite",
        blob_dir=tmp_path / "target" / "blobs",
        data_dir=tmp_path / "target" / "data",
    )

    result = _archive(tmp_path).restore(
        source, passphrase=PASSPHRASE, target=target, confirm=True
    )

    assert result["restored"] is True
    assert (tmp_path / "target" / "kg.sqlite").is_file()
    assert (tmp_path / "target" / "blobs" / "a.bin").read_bytes() == b"blob"
    assert (tmp_path / "target" / "data" / "users.json").read_bytes() == b"[]"
    assert not (tmp_path / "target" / "data" / "rogue.json").exists()
    assert (
        tmp_path / "target" / "data" / "workspace_exports" / "export.json"
    ).read_bytes() == b"{}"
    assert Path(str(result["pre_restore_backup"])).is_dir()
