"""v11.2.0 T7 — a switch in the panel changes what the product does.

A settings screen that stores a preference nobody reads is worse than no
settings screen, because it lies. These are end-to-end: the service persists a
choice, the wiring binds it to the gate that feature actually consults, and the
assertion is on the real behaviour — a node type in SQLite, an HTTP status, the
backend named by ``index_status()`` — never on the stored value.

Every test also asserts the *other* direction, because "on" changing something
is only half the contract: with the switch back off the behaviour must be what
it was before this release, not something new and quiet.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.graph.fusion import (  # noqa: E402
    fusion_strategy_table,
    graph_expansion_enabled,
)
from lattice_brain.graph.store import KnowledgeGraphStore  # noqa: E402
from lattice_brain.ingestion import IngestionItem, IngestionPipeline  # noqa: E402
from lattice_brain.portability import KGPortabilityService  # noqa: E402
from lattice_brain.synthesis import BrainSynthesizer, SynthesisTrigger  # noqa: E402
from latticeai.api.portability import create_portability_router  # noqa: E402
from latticeai.runtime import feature_toggle_wiring as ftw  # noqa: E402
from latticeai.services.feature_toggles import CATALOG  # noqa: E402

ENV_VARS = tuple(item.env_var for item in CATALOG)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    ftw.reset_feature_toggle_service()
    yield
    ftw.reset_feature_toggle_service()


@pytest.fixture
def panel(tmp_path):
    """The switchboard as the app wires it: a service with its gates bound."""
    service = ftw.get_feature_toggle_service(data_dir=tmp_path / "data")
    ftw.bind_feature_gates(service)
    return service


@pytest.fixture
def store(tmp_path):
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


def _png(path: Path, colour="blue") -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 32), colour).save(path)
    return path


# ── 1. multi-modal: the switch changes how a real file is routed ─────────────
def test_turning_on_multimodal_changes_what_a_photo_becomes_in_the_graph(
    panel, store, tmp_path, monkeypatch
):
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    pipeline = IngestionPipeline(store)
    photo = _png(tmp_path / "album" / "photo.png")

    off = pipeline.ingest(
        IngestionItem(source_type="file", path=str(photo), title="photo.png"),
        user_email="me@local",
    )
    assert store.get_node(off.node_id)["type"] == "Document"
    assert pipeline.multimodal_status()["enabled"] is False

    panel.set("allow_multimodal", True)

    on = pipeline.ingest(
        IngestionItem(
            source_type="file", path=str(_png(tmp_path / "album" / "other.png", "red")),
            title="other.png",
        ),
        user_email="me@local",
    )
    assert store.get_node(on.node_id)["type"] == "Image"
    assert pipeline.multimodal_status()["enabled"] is True
    # The same pipeline object: nothing was rebuilt, nothing restarted.
    assert pipeline.multimodal_status()["gates"]["multimodal"]["source"] == "resolver"


def test_the_video_sub_switch_only_matters_while_multimodal_is_on(
    panel, store, tmp_path
):
    pipeline = IngestionPipeline(store)
    video = tmp_path / "standup.mp4"
    video.write_bytes(b"not really a movie")

    panel.set("video_ingest", False)
    panel.set("allow_multimodal", True)
    refused = pipeline.ingest(
        IngestionItem(source_type="video", path=str(video)), user_email="me@local"
    )
    assert refused.status == "unavailable"
    assert "video ingestion is turned off" in refused.detail

    # With multi-modal itself off nothing is *recognized* as video in the first
    # place — the reason a person is shown changes to the switch that actually
    # governs, rather than blaming the sub-switch they never touched.
    panel.set("allow_multimodal", False)
    assert "multi-modal ingestion is off" in pipeline.multimodal_status()["video_detail"]
    assert pipeline.ingest(
        IngestionItem(source_type="video", path=str(video)), user_email="me@local"
    ).status == "ok"


# ── 2. sharing: the switch flips a real 403 ──────────────────────────────────
def _admin(request: Request):
    if request.headers.get("X-Test-Admin") != "true":
        raise HTTPException(status_code=403, detail="admin required")
    return "admin@example.com"


def test_turning_on_sharing_flips_the_share_routes_from_403_to_200(
    panel, store, tmp_path
):
    service = KGPortabilityService(
        knowledge_graph=store, data_dir=tmp_path / "data", enable_graph=True
    )
    app = FastAPI()
    app.include_router(
        create_portability_router(
            service=service,
            require_user=lambda _request: "me@local",
            require_admin=_admin,
        )
    )
    client = TestClient(app)
    admin = {"X-Test-Admin": "true"}

    assert client.get("/api/knowledge-graph/share").json()["enabled"] is False
    assert client.get(
        "/api/knowledge-graph/share/recipient-key", headers=admin
    ).status_code == 403

    panel.set("brain_network", True)

    assert client.get("/api/knowledge-graph/share").json()["enabled"] is True
    allowed = client.get("/api/knowledge-graph/share/recipient-key", headers=admin)
    assert allowed.status_code == 200

    # And back: turning it off closes the door again in the same process.
    panel.set("brain_network", False)
    assert client.get(
        "/api/knowledge-graph/share/recipient-key", headers=admin
    ).status_code == 403


# ── 3. the vector backend choice reaches index_status() ──────────────────────
def test_choosing_a_backend_changes_the_one_index_status_reports(panel, store):
    store.ingest_message(
        "user", "Lattice keeps this note", user_email="me@local",
        conversation_id="c1", source="test",
    )

    assert store.index_status()["storage"]["vector_index"]["name"] == "brute"

    panel.set("vector_backend", "quantized")

    selected = store.index_status()["storage"]["vector_index"]
    assert selected["name"] == "quantized"
    assert selected["honored"] is True
    assert selected["approx"] is True
    assert selected["detail"] is None


# ── 4. automatic vector sync ─────────────────────────────────────────────────
def _watch_incremental_sync(store: KnowledgeGraphStore) -> List[str]:
    """Record every node the pipeline hands to the incremental vector sync."""
    synced: List[str] = []
    original = store.index_node_incremental

    def _recording(node_id: str):
        synced.append(node_id)
        return original(node_id)

    store.index_node_incremental = _recording
    return synced


def test_turning_off_the_automatic_index_stops_the_post_ingest_sync(panel, store):
    pipeline = IngestionPipeline(store)
    synced = _watch_incremental_sync(store)

    first = pipeline.ingest(
        IngestionItem(source_type="note", text="a note that should be searchable"),
        user_email="me@local",
    )
    assert synced == [first.node_id]

    panel.set("auto_vector_index", False)
    second = pipeline.ingest(
        IngestionItem(source_type="note", text="a second note, indexed later"),
        user_email="me@local",
    )

    assert second.status == "ok"
    assert synced == [first.node_id]
    # SQLite is still the source of truth: a rebuild picks up what was skipped.
    assert store.rebuild_vector_index(full=True)["status"] == "completed"


def test_a_constructor_opt_out_still_wins_over_the_panel(panel, store):
    """A caller that said "never" keeps meaning never, however the panel reads."""
    pipeline = IngestionPipeline(store, auto_vector_index=False)
    synced = _watch_incremental_sync(store)
    panel.set("auto_vector_index", True)

    pipeline.ingest(
        IngestionItem(source_type="note", text="explicitly not vector-synced"),
        user_email="me@local",
    )

    assert synced == []


# ── 5. automatic synthesis ───────────────────────────────────────────────────
class _Queue:
    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def create(self, **payload: Any) -> Dict[str, Any]:
        self.items.append(payload)
        return {"id": f"item-{len(self.items)}"}

    def list(self, **_kwargs: Any) -> Dict[str, Any]:
        return {"items": []}


def test_turning_off_self_tidying_stops_the_brain_starting_a_pass_by_itself(
    panel, store
):
    synthesizer = BrainSynthesizer(store, _Queue(), trigger=SynthesisTrigger(threshold=1))
    landed = {"status": "ok", "duplicate": False}

    assert synthesizer.run_if_due(landed) is not None

    panel.set("synthesis", False)
    assert synthesizer.run_if_due(landed) is None
    # Nothing was banked while it was off: the very next ingest after turning it
    # back on has to earn the run on its own.
    assert synthesizer.trigger.status()["pending"] == 0

    panel.set("synthesis", True)
    assert synthesizer.run_if_due(landed) is not None
    # An explicit run a person asked for was never gated.
    panel.set("synthesis", False)
    assert synthesizer.run()["proposed_total"] >= 0


# ── 6. retrieval shape ───────────────────────────────────────────────────────
def test_the_rank_fusion_switch_moves_every_query_class(panel):
    assert set(fusion_strategy_table().values()) == {"alpha"}

    panel.set("fusion_rrf", True)

    assert set(fusion_strategy_table().values()) == {"rrf"}
    # A per-class config is the more specific statement and still wins.
    assert fusion_strategy_table({"code": "alpha"})["code"] == "alpha"


def test_a_per_class_environment_pin_outranks_the_single_switch(panel, monkeypatch):
    monkeypatch.setenv("LATTICEAI_FUSION_STRATEGY", '{"code": "alpha"}')
    panel.set("fusion_rrf", True)

    table = fusion_strategy_table()

    assert table["code"] == "alpha"
    assert table["fact"] == "rrf"


def test_the_neighbour_switch_moves_graph_expansion(panel):
    assert graph_expansion_enabled() is False

    panel.set("graph_expansion", True)

    assert graph_expansion_enabled() is True


def test_the_photo_search_switch_moves_the_search_services_gate(panel, tmp_path):
    from latticeai.services.search_service import SearchService

    service = SearchService(graph_store=object())

    assert service.image_query_status()["gate"]["enabled"] is False

    panel.set("auto_late_fusion", True)

    status = service.image_query_status()
    assert status["gate"]["enabled"] is True
    assert status["gate"]["source"] == "resolver"


def test_the_vault_watch_switch_moves_the_watch_services_refusal(
    panel, store, tmp_path
):
    from latticeai.services.folder_watch import FolderWatchService

    watcher = FolderWatchService(
        pipeline=IngestionPipeline(store), config_path=tmp_path / "watch.json"
    )
    vault = tmp_path / "vault"
    vault.mkdir()

    assert watcher.enable(str(vault), kind="vault")["status"] == "disabled"

    panel.set("vault_watch", True)

    assert watcher.status()["vault_watch"]["enabled"] is True
    assert watcher.enable(str(vault), kind="vault")["status"] != "disabled"
