"""Hermetic coverage for lines that were only covered by the dev machine.

The first v11.0.0 CI runs showed nine lines green on the development Mac and
missing on a clean Linux runner. None were platform branches — they were
accidental coverage: real files under the developer's home (``~/.ltcai``
model dirs, ``~/.ltcai-brain`` vault notes, a persisted device key) leaking
into tests that used default data dirs. These tests pin each line through an
explicit seam so a clean machine measures the same 100%.
"""

from types import SimpleNamespace

import pytest

from lattice_brain import embeddings as brain_embeddings
from lattice_brain.graph import identity as identity_mod
from lattice_brain.graph.store import KnowledgeGraphStore
from latticeai.models import router as models_router

# ``hf_model_dir`` reads the root from its own module globals, so after the
# v11.3.0 split the temp-dir stand-in lands on ``.local_models``.
from latticeai.models.router import local_models as router_local_models
from latticeai.services import p_reinforce as p_reinforce_mod
from latticeai.services.p_reinforce import PReinforceGardener
from latticeai.services.triggers import TriggerService


# ── lattice_brain/graph/identity.py:76-77 — load an existing key file ────────
def test_device_identity_reloads_the_persisted_key_file(tmp_path):
    first = identity_mod.DeviceIdentity(tmp_path, use_keyring=False)
    key_file = tmp_path / "device_identity.key"
    assert key_file.exists()
    persisted = key_file.read_text()

    second = identity_mod.DeviceIdentity(tmp_path, use_keyring=False)

    assert second.storage == "file"
    assert key_file.read_text() == persisted  # loaded, not regenerated
    assert second.public_key_b64 == first.public_key_b64


# ── graph/projection/v2_schema.py:409 — strict edge projection re-raises ────
def test_strict_edge_projection_reraises_the_underlying_failure(tmp_path):
    import sqlite3

    store = KnowledgeGraphStore(db_path=tmp_path / "kg.sqlite3", blob_dir=tmp_path / "blobs")
    dead = sqlite3.connect(tmp_path / "dead.sqlite3")
    dead.close()  # every execute inside the projection's try now raises

    with pytest.raises(sqlite3.ProgrammingError):
        store._v2_project_edge(
            dead, "n1", "n2", "related", 1.0, '{"source": "unit"}', strict=True
        )


# ── lattice_brain/embeddings.py — Korean bigram features ─────────────────────
def test_korean_text_produces_ko_bigram_features():
    features = brain_embeddings._tokenize("한국어임베딩 검증")

    assert "tok:한국어임베딩" in features
    assert "ko:한국" in features
    assert "ko:국어" in features


# ── models/router/local_models.py:51 — locally downloaded HF model dir wins ─
def test_resolve_local_hf_model_finds_the_downloaded_model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(router_local_models, "HF_MODELS_ROOT", tmp_path)
    model_dir = tmp_path / "org__repo"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "weights.safetensors").write_bytes(b"\x00")
    (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

    assert models_router._resolve_local_hf_model("org/repo") == str(model_dir)


# ── latticeai/services/p_reinforce.py:135,173 — failed vault ingest paths ────
def test_vault_import_counts_a_note_the_pipeline_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(p_reinforce_mod, "BRAIN_DIR", tmp_path)
    (tmp_path / "good.md").write_text("# good\nfine note", encoding="utf-8")
    (tmp_path / "poison.md").write_text("# poison\nrefused note", encoding="utf-8")

    class _Pipeline:
        def ingest(self, item):
            if "poison" in (getattr(item, "title", "") or ""):
                return SimpleNamespace(status="error", detail="refused", node_id=None,
                                       provenance_id=None, duplicate=False)
            return SimpleNamespace(status="ok", detail="", node_id="node-1",
                                   provenance_id="prov-1", duplicate=False)

    result = PReinforceGardener(ingestion_pipeline=_Pipeline()).import_vault()

    assert result == {"status": "ok", "imported": 1, "duplicates": 0, "failed": 1}


# ── latticeai/services/triggers.py:107 — non-trigger nodes are skipped ───────
def test_trigger_scan_skips_non_trigger_nodes(tmp_path):
    workflow = {
        "id": "wf-1",
        "name": "mixed nodes",
        "nodes": [
            {"id": "out-1", "type": "output", "config": {}},
            {"id": "trig-1", "type": "trigger",
             "config": {"trigger": "interval", "interval_seconds": 60}},
        ],
    }
    store = SimpleNamespace(load_state=lambda: {"workflows": [workflow]})
    service = TriggerService(
        store=store,
        run_workflow=lambda _wf_id, _payload: {"status": "ok"},
        data_dir=tmp_path,
    )

    triggered = service._triggered_workflows()

    assert [item["workflow"]["id"] for item in triggered] == ["wf-1"]
    assert [item["node"]["id"] for item in triggered] == ["trig-1"]
    assert [item["kind"] for item in triggered] == ["interval"]
