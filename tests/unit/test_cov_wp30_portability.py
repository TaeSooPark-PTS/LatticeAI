"""wp30 coverage — KG portability: swap helpers, verify, status, migration.

Two halves. The module-level swap helpers own the failure paths that matter
during a restore: a rollback is best-effort recovery, so an I/O slip *inside*
it must be logged and must never replace the original error being reported.
:class:`KGPortabilityService` then owns the honest refusals — disabled graph,
invalid artifact, unsafe archive member, missing DSN — plus the pre-migration
backup gate that refuses to start a Postgres migration on an unverifiable
snapshot.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionItem, IngestionPipeline
from lattice_brain.portability import (
    KGPortabilityService,
    _checkpoint_sqlite,
    _pre_restore_backup_dir,
    _replace_sqlite_atomically,
    _replace_tree_with_backup,
    _safe_zip_names,
)


class _FakeKG:
    """Minimal knowledge-graph surface the restore path reads."""

    def __init__(self, db_path: Path, blob_dir: Path) -> None:
        self.db_path = db_path
        self.blob_dir = blob_dir

    def stats(self):
        return {"nodes": {"Document": 2}}


def _seeded_store(tmp_path: Path, tag: str = "kg") -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / f"{tag}.sqlite", tmp_path / f"{tag}-blobs")
    IngestionPipeline(store).ingest(
        IngestionItem(source_type="note", title="A", text="alpha body about graphs")
    )
    return store


def _service(tmp_path: Path, tag: str = "kg") -> KGPortabilityService:
    return KGPortabilityService(
        knowledge_graph=_seeded_store(tmp_path, tag), data_dir=tmp_path / f"{tag}-data"
    )


def _write_zip(path: Path, *, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def _fail_unlink_for(monkeypatch, marker: str) -> None:
    """Make ``Path.unlink`` fail for one specific temp name, nothing else."""
    real = Path.unlink

    def fake(self, *args, **kwargs):
        if marker in self.name:
            raise OSError("simulated cleanup failure")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake)


# ── module helpers ───────────────────────────────────────────────────────────

def test_safe_zip_names_rejects_traversal_and_absolute_members():
    _safe_zip_names(["a/b.txt", "manifest.json"])
    with pytest.raises(ValueError, match="unsafe path"):
        _safe_zip_names(["../escape.txt"])
    with pytest.raises(ValueError, match="unsafe path"):
        _safe_zip_names(["/etc/passwd"])


def test_pre_restore_backup_dir_suffixes_until_it_finds_a_free_name(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lattice_brain.portability.fsops._stamp", lambda: "20260809T0000"
    )
    anchor = tmp_path / "kg.sqlite"
    first = _pre_restore_backup_dir(anchor)
    second = _pre_restore_backup_dir(anchor)
    third = _pre_restore_backup_dir(anchor)
    assert first.name == "kg.sqlite.pre-restore-20260809T0000"
    assert second.name == "kg.sqlite.pre-restore-20260809T0000-1"
    assert third.name == "kg.sqlite.pre-restore-20260809T0000-2"
    assert first.is_dir() and second.is_dir() and third.is_dir()


def test_checkpoint_sqlite_is_best_effort(tmp_path):
    # Absent database: nothing to checkpoint, no error.
    _checkpoint_sqlite(tmp_path / "missing.sqlite")
    # Present but not a database: the checkpoint is skipped, never raised.
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"this is not a database at all")
    _checkpoint_sqlite(broken)
    assert broken.read_bytes().startswith(b"this is not")


def test_sqlite_swap_rollback_failures_are_logged_and_never_mask_the_original(
    tmp_path, monkeypatch, caplog
):
    src = tmp_path / "src.sqlite"
    with sqlite3.connect(str(src)) as conn:
        conn.execute("CREATE TABLE t (a)")
    # A directory where the database belongs: copying it as a sibling raises,
    # and every rollback step then fails too.
    dest = tmp_path / "live" / "kg.sqlite"
    dest.mkdir(parents=True)
    backup_dir = tmp_path / "pre-restore"
    backup_dir.mkdir()
    _fail_unlink_for(monkeypatch, ".restore-")

    with caplog.at_level("WARNING"), pytest.raises(OSError):
        _replace_sqlite_atomically(src, dest, backup_dir)

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "restore tmp cleanup failed" in logged
    assert "restore sibling rollback incomplete" in logged
    assert dest.is_dir()  # untouched — the swap never happened


def test_blob_tree_rollback_failure_is_logged_and_original_error_propagates(
    tmp_path, caplog
):
    src = tmp_path / "incoming"
    (src / "nested").mkdir(parents=True)
    (src / "nested" / "a.bin").write_bytes(b"new")
    dest = tmp_path / "blobs"
    dest.mkdir()
    (dest / "old.bin").write_bytes(b"old")
    backup_dir = tmp_path / "pre-restore"
    backup_dir.mkdir()
    # The backup slot is already taken by a *file*, so copytree(dest, backup)
    # fails and the rollback's copytree(backup, dest) fails too.
    (backup_dir / "blobs").write_bytes(b"not a directory")

    with caplog.at_level("WARNING"), pytest.raises(OSError):
        _replace_tree_with_backup(src, dest, backup_dir)

    assert "blob tree rollback incomplete" in " ".join(
        record.getMessage() for record in caplog.records
    )


def test_replace_tree_with_backup_stages_an_empty_dir_when_no_source(tmp_path):
    dest = tmp_path / "blobs"
    dest.mkdir()
    (dest / "stale.bin").write_bytes(b"stale")
    backup_dir = tmp_path / "pre-restore"
    backup_dir.mkdir()

    _replace_tree_with_backup(None, dest, backup_dir)

    assert dest.is_dir()
    assert list(dest.iterdir()) == []
    assert (backup_dir / "blobs" / "stale.bin").read_bytes() == b"stale"


# ── disabled-graph honesty ───────────────────────────────────────────────────

def test_disabled_graph_refuses_writes_and_reports_absence(tmp_path):
    svc = KGPortabilityService(knowledge_graph=None, data_dir=tmp_path / "data")
    assert svc.available() is False
    with pytest.raises(RuntimeError, match="LATTICEAI_ENABLE_GRAPH"):
        svc.export()
    assert svc.snapshot_metadata() == {"available": False}
    assert svc.storage_status() == {"available": False}
    assert svc.recent_ingestions() == {"items": [], "count": 0}


# ── logical export / import ──────────────────────────────────────────────────

def test_export_to_file_roundtrips_through_import_from_file(tmp_path):
    svc = _service(tmp_path, "src")
    written = svc.export_to_file()
    path = Path(written["path"])
    assert path.parent.name == "workspace_exports"
    assert written["bytes"] == path.stat().st_size
    assert written["header"]["format"] == "latticeai.kg.export"

    explicit = svc.export_to_file(tmp_path / "explicit.json")
    assert Path(explicit["path"]) == tmp_path / "explicit.json"

    dst = KnowledgeGraphStore(tmp_path / "dst.sqlite", tmp_path / "dst-blobs")
    result = KGPortabilityService(
        knowledge_graph=dst, data_dir=tmp_path / "dst-data"
    ).import_from_file(tmp_path / "explicit.json")
    assert result["imported"] is True
    assert result["origin"] == "unsigned-legacy"
    assert sum(dst.stats().get("nodes", {}).values()) >= 1


def test_import_data_rejects_bad_artifacts_and_modes(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(ValueError, match="Invalid Knowledge Graph export artifact"):
        svc.import_data("not a dict")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Invalid Knowledge Graph export artifact"):
        svc.import_data({"edges": []})
    with pytest.raises(ValueError, match="mode must be"):
        svc.import_data({"nodes": []}, mode="overwrite")


def test_import_survives_a_broken_provenance_table(tmp_path):
    store = _seeded_store(tmp_path, "src")
    artifact = KGPortabilityService(
        knowledge_graph=store, data_dir=tmp_path / "src-data"
    ).export()

    dst = KnowledgeGraphStore(tmp_path / "dst.sqlite", tmp_path / "dst-blobs")

    def _boom(**kwargs):
        raise sqlite3.OperationalError("no such table: ingestion_provenance")

    dst.record_provenance = _boom  # type: ignore[method-assign]
    result = KGPortabilityService(
        knowledge_graph=dst, data_dir=tmp_path / "dst-data"
    ).import_data(artifact)
    assert result["imported"] is True
    assert sum(dst.stats().get("nodes", {}).values()) >= 1


# ── binary restore ───────────────────────────────────────────────────────────

def test_restore_refuses_missing_and_incomplete_archives(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(FileNotFoundError, match="Backup archive not found"):
        svc.restore(tmp_path / "nope.zip", confirm=True)

    empty = _write_zip(tmp_path / "empty.zip", members={"manifest.json": "{}"})
    with pytest.raises(ValueError, match="missing knowledge_graph.sqlite"):
        svc.restore(empty, confirm=True)


def test_restore_rollback_failure_is_logged_and_original_error_propagates(
    tmp_path, caplog
):
    archive = _write_zip(
        tmp_path / "backup.zip",
        members={"knowledge_graph.sqlite": b"SQLite format 3\x00", "manifest.json": "{}"},
    )
    # db_path points at a directory: every swap and rollback step fails.
    db_dir = tmp_path / "live" / "knowledge_graph.sqlite"
    db_dir.mkdir(parents=True)
    svc = KGPortabilityService(
        knowledge_graph=_FakeKG(db_dir, tmp_path / "live" / "blobs"),
        data_dir=tmp_path / "data",
    )

    with caplog.at_level("WARNING"), pytest.raises(OSError):
        svc.restore(archive, confirm=True)

    assert "pre-restore rollback incomplete" in " ".join(
        record.getMessage() for record in caplog.records
    )
    assert db_dir.is_dir()


# ── verify_backup ────────────────────────────────────────────────────────────

def test_verify_backup_accepts_a_real_backup(tmp_path):
    svc = _service(tmp_path)
    made = svc.backup()
    verdict = svc.verify_backup(made["path"])
    assert verdict == {
        "ok": True,
        "path": made["path"],
        "manifest": made["manifest"],
        "errors": [],
    }


def test_verify_backup_reports_every_rejection_as_data_not_an_exception(tmp_path):
    svc = _service(tmp_path)

    absent = svc.verify_backup(tmp_path / "gone.zip")
    assert absent["ok"] is False
    assert "not found" in absent["errors"][0]

    unsafe = _write_zip(
        tmp_path / "unsafe.zip",
        members={"knowledge_graph.sqlite": b"db", "../escape.txt": b"x"},
    )
    assert svc.verify_backup(unsafe)["errors"][0].startswith(
        "Backup archive contains unsafe path"
    )

    incomplete = _write_zip(tmp_path / "incomplete.zip", members={"manifest.json": "{}"})
    assert "missing knowledge_graph.sqlite" in svc.verify_backup(incomplete)["errors"][0]

    tampered = _write_zip(
        tmp_path / "tampered.zip",
        members={
            "knowledge_graph.sqlite": b"tampered bytes",
            "manifest.json": json.dumps({"db_sha256": "0" * 64}),
        },
    )
    assert "integrity check failed" in svc.verify_backup(tampered)["errors"][0]

    not_a_zip = tmp_path / "plain.zip"
    not_a_zip.write_bytes(b"not a zip file")
    assert svc.verify_backup(not_a_zip)["ok"] is False


# ── storage / migration surface ──────────────────────────────────────────────

def test_postgres_docker_setup_requires_consent_and_never_runs_docker(tmp_path):
    svc = _service(tmp_path)
    plan = svc.postgres_docker_setup(consent=False, dry_run=True, port=55432)
    assert plan["status"] == "consent_required"
    assert plan["started"] is False
    assert Path(plan["compose_path"]).is_file()
    assert Path(plan["compose_path"]).parent == (tmp_path / "kg-data" / "postgres")


def test_migration_requires_a_dsn(tmp_path):
    svc = _service(tmp_path)
    with pytest.raises(ValueError, match="Postgres DSN is required"):
        svc.migrate_sqlite_to_postgres(dsn="")


def test_migration_refuses_to_start_when_the_pre_migration_backup_is_unverifiable(
    tmp_path, monkeypatch
):
    svc = _service(tmp_path)
    counter = {"n": 0}

    def _drifting_hash(path):
        counter["n"] += 1
        return f"{counter['n']:064d}"

    monkeypatch.setattr(
        "lattice_brain.portability.backups._sha256_file", _drifting_hash
    )
    with pytest.raises(RuntimeError, match="Pre-migration backup verification failed"):
        svc.migrate_sqlite_to_postgres(dsn="postgresql://localhost/lattice", dry_run=False)


def test_migration_attaches_the_verified_pre_migration_backup(tmp_path, monkeypatch):
    svc = _service(tmp_path)
    seen = {}

    class _FakeMigrator:
        def __init__(self, sqlite_path, target):
            seen["sqlite_path"] = Path(sqlite_path)
            seen["target"] = target

        def migrate(self, *, dry_run=False):
            seen["dry_run"] = dry_run
            return {"status": "planned" if dry_run else "migrated", "tables": 3}

    monkeypatch.setattr(
        "lattice_brain.portability.backups.SQLiteToPostgresMigrator", _FakeMigrator
    )

    planned = svc.migrate_sqlite_to_postgres(dsn="postgresql://localhost/lattice")
    assert planned == {"status": "planned", "tables": 3}
    assert "pre_migration_backup" not in planned

    result = svc.migrate_sqlite_to_postgres(
        dsn="postgresql://localhost/lattice", dry_run=False
    )
    assert seen["dry_run"] is False
    assert seen["sqlite_path"] == Path(svc._kg.db_path)
    assert result["status"] == "migrated"
    backup = result["pre_migration_backup"]
    assert backup["verified"] is True
    assert Path(backup["path"]).is_file()
    assert backup["manifest"]["format"] == "latticeai.kg.backup"
