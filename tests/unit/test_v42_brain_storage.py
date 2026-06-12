from __future__ import annotations

import sqlite3
import json
from pathlib import Path

import pytest

from lattice_brain import BrainCore, KnowledgeGraphStore
from lattice_brain.archive import BrainArchivePaths, EncryptedBrainArchive
from lattice_brain.storage import (
    DockerPostgresWizard,
    PostgresEngine,
    SQLiteEngine,
    SQLiteToPostgresMigrator,
    StorageUnavailable,
    storage_from_env,
)
from latticeai.services.kg_portability import KGPortabilityService


def test_lattice_brain_package_exports_working_store(tmp_path: Path):
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")
    doc = tmp_path / "brain.txt"
    doc.write_text("storage abstraction vector search migration archive", encoding="utf-8")

    result = store.ingest_document(
        doc,
        original_filename="brain.txt",
        extracted={"content": doc.read_text(encoding="utf-8")},
    )
    store.rebuild_vector_index(full=True)
    vector = store.vector_search("storage migration", limit=3)
    status = store.index_status()

    assert result["node_id"]
    assert vector["matches"], "hash-vector fallback must be real retrieval, not fake availability"
    assert status["storage"]["engine"]["engine"] == "sqlite"
    assert status["storage"]["vector_search_backend"] in {"sqlite-vec", "bruteforce-cosine"}


def test_brain_core_constructs_sqlite_graph_and_conversation_store(tmp_path: Path):
    core = BrainCore.from_paths(tmp_path)

    core.conversations.append(
        {
            "role": "user",
            "content": "hello brain",
            "timestamp": "2026-06-12T00:00:00Z",
            "conversation_id": "c1",
        }
    )

    assert core.status()["storage"]["engine"] == "sqlite"
    assert core.knowledge.stats()["db_path"].endswith("knowledge_graph.sqlite")
    assert core.conversations.history(conversation_id="c1")[0]["content"] == "hello brain"


def test_storage_from_env_defaults_sqlite_and_explicit_postgres_needs_dsn(tmp_path: Path):
    assert isinstance(storage_from_env({}, data_dir=tmp_path), SQLiteEngine)
    with pytest.raises(StorageUnavailable):
        storage_from_env({"LATTICEAI_STORAGE_ENGINE": "postgres"}, data_dir=tmp_path)


def test_sqlite_to_postgres_migration_plan_preserves_all_user_tables(tmp_path: Path):
    db = tmp_path / "brain.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE nodes(id TEXT PRIMARY KEY, title TEXT, metadata_json TEXT)")
        conn.execute("INSERT INTO nodes(id, title, metadata_json) VALUES ('n1', 'Node', '{}')")
        conn.execute("CREATE TABLE conversation_messages(role TEXT, content TEXT)")
        conn.execute("INSERT INTO conversation_messages(role, content) VALUES ('user', 'hello')")
        conn.execute("CREATE TABLE rowidless_idx(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID")
        conn.execute("INSERT INTO rowidless_idx(segid, term, pgno) VALUES (1, 'brain', 1)")

    migrator = SQLiteToPostgresMigrator(db, PostgresEngine("postgresql://example.invalid/db"))
    plan = migrator.migrate(dry_run=True)

    assert plan["status"] == "planned"
    assert plan["total_rows"] == 3
    tables = {table["name"]: table for table in plan["tables"]}
    assert tables["nodes"]["conflict_key"] == "id"
    assert tables["conversation_messages"]["conflict_key"] == "__source_rowid"
    assert tables["rowidless_idx"]["conflict_columns"] == ["segid", "term"]
    assert tables["rowidless_idx"]["rowid_available"] is False


def test_docker_postgres_wizard_never_starts_without_consent(tmp_path: Path):
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("docker must not run without explicit consent")

    result = DockerPostgresWizard(tmp_path).start(consent=False, runner=runner)

    assert result["status"] == "consent_required"
    assert result["started"] is False
    assert calls == []
    assert (tmp_path / "postgres.compose.yml").exists()


def test_encrypted_latticebrain_archive_round_trip(tmp_path: Path):
    db = tmp_path / "knowledge_graph.sqlite"
    blobs = tmp_path / "knowledge_graph_blobs"
    data_dir = tmp_path / "data"
    blobs.mkdir()
    data_dir.mkdir()
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE nodes(id TEXT PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO nodes(id, title) VALUES ('n1', 'Encrypted')")
    (blobs / "note.txt").write_text("owned by user", encoding="utf-8")
    (data_dir / "workspace_os.json").write_text('{"active_workspace": "personal"}', encoding="utf-8")
    (data_dir / "device_identity.key").write_text("private-key-must-not-export", encoding="utf-8")
    exports = data_dir / "workspace_exports"
    exports.mkdir()
    (exports / "kg-export-demo.json").write_text('{"signature": {"fingerprint": "demo"}}', encoding="utf-8")

    archive = EncryptedBrainArchive(
        BrainArchivePaths(
            db_path=db,
            blob_dir=blobs,
            data_dir=data_dir,
            metadata={"storage": {"engine": "sqlite"}, "device_identity": {"fingerprint": "demo"}},
        )
    )
    out = archive.create(tmp_path / "backup.latticebrain", passphrase="correct horse battery staple")
    inspected = archive.inspect(Path(out["path"]), passphrase="correct horse battery staple")
    verified = archive.verify(Path(out["path"]), passphrase="correct horse battery staple")

    assert inspected["verified"] is True
    assert verified["ok"] is True
    assert verified["manifest"]["sections"]["workspace_state"] is True
    assert verified["manifest"]["sections"]["signed_bundles"] is True
    assert all("device_identity.key" not in entry["path"] for entry in verified["manifest"]["entries"])

    db.unlink()
    restored_blobs = tmp_path / "restored_blobs"
    restored_data = tmp_path / "restored_data"
    dry_run = archive.restore(
        Path(out["path"]),
        passphrase="correct horse battery staple",
        target=BrainArchivePaths(db_path=db, blob_dir=restored_blobs, data_dir=restored_data),
        dry_run=True,
    )
    assert dry_run["dry_run"] is True
    assert not db.exists()

    with pytest.raises(ValueError, match="confirmation"):
        archive.restore(
            Path(out["path"]),
            passphrase="correct horse battery staple",
            target=BrainArchivePaths(db_path=db, blob_dir=restored_blobs, data_dir=restored_data),
        )

    archive.restore(
        Path(out["path"]),
        passphrase="correct horse battery staple",
        target=BrainArchivePaths(db_path=db, blob_dir=restored_blobs, data_dir=restored_data),
        confirm=True,
    )

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT title FROM nodes WHERE id='n1'").fetchone()[0] == "Encrypted"
    assert (restored_blobs / "note.txt").read_text(encoding="utf-8") == "owned by user"
    assert (restored_data / "workspace_os.json").exists()
    assert (restored_data / "workspace_exports" / "kg-export-demo.json").exists()
    assert not (restored_data / "device_identity.key").exists()

    with pytest.raises(ValueError):
        archive.restore(
            Path(out["path"]),
            passphrase="wrong",
            target=BrainArchivePaths(db_path=tmp_path / "bad.sqlite"),
            confirm=True,
        )


def test_portability_service_exposes_storage_and_archive_paths(tmp_path: Path):
    core = BrainCore.from_paths(tmp_path)
    service = KGPortabilityService(knowledge_graph=core.knowledge, data_dir=tmp_path)

    status = service.storage_status()
    planned = service.migrate_sqlite_to_postgres(
        dsn="postgresql://example.invalid/db",
        dry_run=True,
    )
    archive = service.encrypted_archive(passphrase="archive passphrase")
    verified = service.verify_encrypted_archive(archive["path"], passphrase="archive passphrase")
    dry_restore = service.restore_encrypted_archive(
        archive["path"],
        passphrase="archive passphrase",
        dry_run=True,
    )

    assert status["active"]["engine"] == "sqlite"
    assert status["postgres"]["available"] is False
    assert planned["status"] == "planned"
    assert Path(archive["path"]).suffix == ".latticebrain"
    assert verified["ok"] is True
    assert dry_restore["dry_run"] is True


def test_encrypted_latticebrain_archive_fails_closed_on_tamper_and_newer_version(tmp_path: Path):
    db = tmp_path / "knowledge_graph.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE nodes(id TEXT PRIMARY KEY)")
    archive = EncryptedBrainArchive(BrainArchivePaths(db_path=db))
    out = archive.create(tmp_path / "brain.latticebrain", passphrase="passphrase")

    raw = json.loads(Path(out["path"]).read_text(encoding="utf-8"))
    raw["payload"] = raw["payload"][:-4] + "AAAA"
    tampered = tmp_path / "tampered.latticebrain"
    tampered.write_text(json.dumps(raw), encoding="utf-8")
    assert archive.verify(tampered, passphrase="passphrase")["ok"] is False

    raw = json.loads(Path(out["path"]).read_text(encoding="utf-8"))
    raw["format_version"] = 99
    newer = tmp_path / "newer.latticebrain"
    newer.write_text(json.dumps(raw), encoding="utf-8")
    assert archive.verify(newer, passphrase="passphrase")["ok"] is False
