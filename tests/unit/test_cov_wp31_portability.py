"""wp31: the portability router's storage, import/backup/restore and error paths.

``tests/unit/test_kg_portability.py`` proves the admin gate and the export /
archive happy paths. What never ran here: the storage + backup-health reads, the
file export, import, backup, restore, the Postgres routes, the shared 503 guard,
and every ``(ValueError, FileNotFoundError)`` → 400 translation.

The service is the real :class:`~lattice_brain.portability.KGPortabilityService`
over a seeded store in ``tmp_path``; the Postgres routes are exercised in their
no-side-effect modes (consent withheld / dry run), so nothing starts a container
or opens a socket.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from lattice_brain.graph.store import KnowledgeGraphStore
from lattice_brain.ingestion import IngestionItem, IngestionPipeline
from lattice_brain.portability import KGPortabilityService
from latticeai.api.portability import create_portability_router

EN = {"Accept-Language": "en"}


def _seeded(tmp_path: Path) -> KnowledgeGraphStore:
    store = KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "kg-blobs")
    pipe = IngestionPipeline(store)
    pipe.ingest(
        IngestionItem(
            source_type="web_url",
            title="Alpha",
            text="alpha body about graphs",
            source_uri="u1",
        )
    )
    pipe.ingest(IngestionItem(source_type="note", title="Beta", text="beta body TODO decide"))
    return store


def _client(service) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_portability_router(
            service=service,
            require_user=lambda request: "user@example.com",
            require_admin=lambda request: ("admin@example.com", {}),
        )
    )
    return TestClient(app)


@pytest.fixture()
def live(tmp_path):
    service = KGPortabilityService(
        knowledge_graph=_seeded(tmp_path), data_dir=tmp_path / "data"
    )
    return _client(service), service, tmp_path


@pytest.fixture()
def disabled(tmp_path):
    """The router over a service whose knowledge graph is switched off."""
    return _client(
        KGPortabilityService(knowledge_graph=None, data_dir=tmp_path / "data")
    )


_ALL_ROUTES = [
    ("GET", "/api/knowledge-graph/portability", None),
    ("GET", "/api/brain/storage", None),
    ("GET", "/api/knowledge-graph/backup-health", None),
    ("GET", "/api/knowledge-graph/provenance", None),
    ("POST", "/api/knowledge-graph/export", None),
    ("POST", "/api/knowledge-graph/export-file", None),
    ("POST", "/api/knowledge-graph/import", {"artifact": {}}),
    ("POST", "/api/knowledge-graph/backup", {}),
    ("POST", "/api/knowledge-graph/restore", {"path": "x"}),
    ("POST", "/api/knowledge-graph/archive", {"passphrase": "p"}),
    ("POST", "/api/knowledge-graph/archive/inspect", {"path": "x"}),
    ("POST", "/api/knowledge-graph/archive/verify", {"path": "x", "passphrase": "p"}),
    ("POST", "/api/knowledge-graph/archive/import", {"path": "x", "passphrase": "p"}),
    ("POST", "/api/knowledge-graph/archive/restore", {"path": "x", "passphrase": "p"}),
    ("POST", "/api/brain/storage/postgres/docker", {}),
    ("POST", "/api/brain/storage/migrate-postgres", {"dsn": "postgresql://x"}),
]


@pytest.mark.parametrize(("method", "path", "payload"), _ALL_ROUTES)
def test_every_route_503s_when_the_graph_is_disabled(disabled, method, path, payload):
    response = disabled.request(method, path, json=payload, headers=EN)

    assert response.status_code == 503
    assert response.json()["detail"] == "The Knowledge Graph is turned off."


def test_storage_status_reports_the_active_engine_and_backup_health(live):
    client, _service, _tmp = live

    body = client.get("/api/brain/storage").json()

    assert body["available"] is True
    assert body["active"]["engine"] in {"sqlite", "postgres"}
    assert body["postgres"]["engine"] == "postgres"
    assert body["backup_health"]["count"] == 0


def test_backup_health_counts_archives_after_a_backup(live):
    client, _service, _tmp = live

    before = client.get("/api/knowledge-graph/backup-health").json()
    client.post("/api/knowledge-graph/backup", json={})
    after = client.get("/api/knowledge-graph/backup-health").json()

    assert before["count"] == 0
    assert before["latest"] is None
    assert after["count"] == 1
    assert after["zip_backups"] == 1
    assert after["latest_bytes"] > 0


def test_export_file_writes_an_artifact_next_to_the_backups(live):
    client, _service, _tmp = live

    body = client.post("/api/knowledge-graph/export-file").json()

    assert Path(body["path"]).exists()
    assert body["header"]["format"] == "latticeai.kg.export"
    assert body["bytes"] > 0


def test_import_round_trips_an_export_and_rejects_a_malformed_artifact(live, tmp_path):
    client, _service, _tmp = live
    artifact = client.post("/api/knowledge-graph/export").json()

    target = KGPortabilityService(
        knowledge_graph=KnowledgeGraphStore(
            tmp_path / "dst.sqlite", tmp_path / "dst-blobs"
        ),
        data_dir=tmp_path / "dst-data",
    )
    target_client = _client(target)

    planned = target_client.post(
        "/api/knowledge-graph/import", json={"artifact": artifact, "dry_run": True}
    ).json()
    applied = target_client.post(
        "/api/knowledge-graph/import", json={"artifact": artifact, "mode": "merge"}
    ).json()
    rejected = target_client.post(
        "/api/knowledge-graph/import",
        json={"artifact": {"header": {"format": "not-a-lattice-export"}}},
    )

    assert planned["nodes"] == artifact["counts"]["nodes"]
    assert applied["nodes"] == artifact["counts"]["nodes"]
    assert rejected.status_code == 400


def test_backup_then_restore_recovers_the_graph(live):
    client, service, _tmp = live

    backup = client.post("/api/knowledge-graph/backup", json={}).json()
    dry_run = client.post(
        "/api/knowledge-graph/restore", json={"path": backup["path"], "dry_run": True}
    ).json()
    restored = client.post(
        "/api/knowledge-graph/restore", json={"path": backup["path"], "confirm": True}
    ).json()

    assert Path(backup["path"]).exists()
    assert dry_run["dry_run"] is True
    assert restored["restored"] is True
    assert service.available() is True


def test_restore_reports_a_missing_archive_as_400(live):
    client, _service, tmp_path = live

    response = client.post(
        "/api/knowledge-graph/restore",
        json={"path": str(tmp_path / "nope.zip"), "confirm": True},
    )

    assert response.status_code == 400


def test_archive_creation_failure_translates_to_400():
    """A missing Brain database surfaces as a 400, not a 500."""

    class FailingService:
        def available(self):
            return True

        def encrypted_archive(self, path, *, passphrase):
            raise FileNotFoundError("Brain database not found: /nowhere/kg.sqlite")

    response = _client(FailingService()).post(
        "/api/knowledge-graph/archive", json={"passphrase": "pw"}
    )

    assert response.status_code == 400
    assert "Brain database not found" in response.json()["detail"]


def test_encrypted_archive_errors_translate_to_400(live, tmp_path):
    client, _service, _tmp = live
    missing = str(tmp_path / "absent.latticebrain")

    inspected = client.post(
        "/api/knowledge-graph/archive/inspect", json={"path": missing}
    )
    imported = client.post(
        "/api/knowledge-graph/archive/import",
        json={"path": missing, "passphrase": "pw", "dry_run": True},
    )
    restored = client.post(
        "/api/knowledge-graph/archive/restore",
        json={"path": missing, "passphrase": "pw", "confirm": True},
    )

    assert inspected.status_code == 400
    assert imported.status_code == 400
    assert restored.status_code == 400


def test_verify_reports_a_bad_passphrase_as_400(live, tmp_path):
    client, _service, _tmp = live
    archive = client.post(
        "/api/knowledge-graph/archive", json={"passphrase": "correct horse"}
    ).json()

    ok = client.post(
        "/api/knowledge-graph/archive/verify",
        json={"path": archive["path"], "passphrase": "correct horse"},
    )
    bad = client.post(
        "/api/knowledge-graph/archive/verify",
        json={"path": archive["path"], "passphrase": "wrong"},
        headers=EN,
    )

    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert bad.status_code == 400
    assert bad.json()["detail"]


def test_encrypted_archive_round_trips_through_import_and_restore(live):
    client, _service, _tmp = live
    archive = client.post(
        "/api/knowledge-graph/archive", json={"passphrase": "pass phrase"}
    ).json()

    inspected = client.post(
        "/api/knowledge-graph/archive/inspect",
        json={"path": archive["path"], "passphrase": "pass phrase"},
    ).json()
    imported = client.post(
        "/api/knowledge-graph/archive/import",
        json={"path": archive["path"], "passphrase": "pass phrase", "dry_run": True},
    ).json()
    restored = client.post(
        "/api/knowledge-graph/archive/restore",
        json={"path": archive["path"], "passphrase": "pass phrase", "confirm": True},
    ).json()

    assert inspected["valid_envelope"] is True
    assert inspected["verified"] is True
    assert inspected["errors"] == []
    assert imported
    assert restored["restored"] is True


def test_postgres_docker_setup_withholds_action_without_consent(live):
    client, _service, _tmp = live

    refused = client.post("/api/brain/storage/postgres/docker", json={}).json()
    planned = client.post(
        "/api/brain/storage/postgres/docker",
        json={"consent": True, "dry_run": True, "port": 5433},
    ).json()

    assert refused["status"] == "consent_required"
    assert refused["started"] is False
    assert planned["status"] == "dry_run"
    assert planned["started"] is False
    assert Path(planned["compose_path"]).exists()


def test_migrate_to_postgres_plans_without_connecting_and_requires_a_dsn(live):
    client, _service, _tmp = live

    planned = client.post(
        "/api/brain/storage/migrate-postgres",
        json={"dsn": "postgresql://localhost/lattice", "dry_run": True},
    ).json()
    refused = client.post(
        "/api/brain/storage/migrate-postgres", json={"dsn": "", "dry_run": True}
    )

    assert planned["status"] == "planned"
    assert planned["tables"]
    assert refused.status_code == 400
    assert "DSN" in refused.json()["detail"]


def test_provenance_lists_recent_ingestions(live):
    client, _service, _tmp = live

    body = client.get(
        "/api/knowledge-graph/provenance", params={"limit": 10}
    ).json()

    assert body["count"] >= 2
    assert all("source_type" in item for item in body["items"])


def test_portability_status_is_available_for_a_live_graph(live):
    client, _service, _tmp = live

    body = client.get("/api/knowledge-graph/portability").json()

    assert body["available"] is True


def test_admin_only_routes_reject_a_non_admin(tmp_path):
    service = KGPortabilityService(
        knowledge_graph=_seeded(tmp_path), data_dir=tmp_path / "data"
    )

    def require_admin(request: Request):
        raise HTTPException(status_code=403, detail="admin only")

    app = FastAPI()
    app.include_router(
        create_portability_router(
            service=service,
            require_user=lambda request: "user@example.com",
            require_admin=require_admin,
        )
    )
    client = TestClient(app)

    assert client.post("/api/brain/storage/postgres/docker", json={}).status_code == 403
    assert (
        client.post(
            "/api/brain/storage/migrate-postgres", json={"dsn": "postgresql://x"}
        ).status_code
        == 403
    )
    # Reads that only need a signed-in user still answer.
    assert client.get("/api/brain/storage").status_code == 200
