from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

from latticeai.core.embedding_providers import (
    DEFAULT_FALLBACK_DIM,
    EMBEDDINGGEMMA_PREFIXES,
    FallbackTextEmbedder,
    HashEmbeddingProvider,
    build_embedding_provider,
)
from latticeai.core.embedding_providers.text import _extract_fallback_features


def test_fallback_text_embedder_defaults_to_768d_and_is_unit_normalized() -> None:
    embedder = FallbackTextEmbedder()
    assert embedder.dim == DEFAULT_FALLBACK_DIM == 768
    assert embedder.model_id == "lattice-fallback-subword-v2:768"
    assert embedder.provider == "hash"
    assert embedder.grade == "fallback"

    vec = embedder.embed("Hybrid BM25F + Vector + Knowledge Graph RRF")
    assert len(vec) == 768
    norm = math.sqrt(sum(v * v for v in vec))
    assert math.isclose(norm, 1.0, rel_tol=1e-6)
    assert embedder.embed("Hybrid BM25F + Vector + Knowledge Graph RRF") == vec


def test_fallback_text_embedder_extracts_cjk_bigrams_korean_stems_and_char_ngrams() -> (
    None
):
    features = dict(
        _extract_fallback_features(
            "지식그래프에서 하이브리드 검색을 수행하는 retrieving_engine"
        )
    )
    assert "stem:지식그래프" in features
    assert "ko2:지식" in features
    assert "ko2:그래" in features
    assert "ko2:래프" in features
    assert "stem:검색" in features
    assert "tok:retrieving" in features
    assert "tok:engine" in features
    assert any(k.startswith("g3:") for k in features)
    assert any(k.startswith("g4:") for k in features)
    assert any(k.startswith("g5:") for k in features)
    assert any(k.startswith("bi:") for k in features)


def test_fallback_text_embedder_korean_and_english_semantic_subword_similarity() -> (
    None
):
    embedder = FallbackTextEmbedder(dim=768)

    q_ko = embedder.embed("지식 그래프에서 하이브리드 검색을 최적화하는 방법")
    rel_ko = embedder.embed("지식그래프 하이브리드 검색 성능 최적화 가이드")
    unrel_ko = embedder.embed("회계 분기별 세금 신고서 양식 제출 안내")

    sim_ko_rel = embedder.similarity(q_ko, rel_ko)
    sim_ko_unrel = embedder.similarity(q_ko, unrel_ko)
    assert sim_ko_rel > 0.45, (
        f"expected high Korean subword similarity, got {sim_ko_rel}"
    )
    assert sim_ko_rel > sim_ko_unrel + 0.30

    q_en = embedder.embed("retrieving documents from the vector index")
    rel_en = embedder.embed("document retrieval with vector indexing")
    unrel_en = embedder.embed("css animation keyframe button border radius")

    sim_en_rel = embedder.similarity(q_en, rel_en)
    sim_en_unrel = embedder.similarity(q_en, unrel_en)
    assert sim_en_rel > 0.40, (
        f"expected high English subword similarity, got {sim_en_rel}"
    )
    assert sim_en_rel > sim_en_unrel + 0.25


def test_fallback_text_embedder_strips_instruction_prefixes_for_query_passage_alignment() -> (
    None
):
    embedder = FallbackTextEmbedder(dim=768)
    raw_query = "Okapi BM25F 한국어 토크나이저"
    prefixed_query = f"{EMBEDDINGGEMMA_PREFIXES['query']}{raw_query}"
    prefixed_passage = f"{EMBEDDINGGEMMA_PREFIXES['passage']}{raw_query}"

    v_raw = embedder.embed(raw_query)
    v_q = embedder.embed(prefixed_query)
    v_p = embedder.embed(prefixed_passage)

    assert v_raw == v_q == v_p


def test_hash_embedding_provider_and_fallback_subword_embedder_preserve_contracts() -> (
    None
):
    from lattice_brain.embeddings import LocalEmbeddingModel

    prov = build_embedding_provider("hash", dim=384)
    assert isinstance(prov, HashEmbeddingProvider)
    assert isinstance(prov, FallbackTextEmbedder)
    assert prov.dim == 384
    assert prov.model_id == "lattice-local-hash-v1:384"
    assert prov.embed("knowledge_graph_traversal") == LocalEmbeddingModel(
        dim=384
    ).embed("knowledge_graph_traversal")

    fallback = FallbackTextEmbedder(dim=384)
    assert fallback.model_id == "lattice-fallback-subword-v2:384"
    v1 = fallback.embed("knowledge_graph_traversal")
    v2 = fallback.embed("knowledge graph traversal")
    v3 = fallback.embed("unrelated audio waveform filter")
    assert fallback.similarity(v1, v2) > 0.50
    assert fallback.similarity(v1, v2) > fallback.similarity(v1, v3) + 0.30


def test_bm25_node_stats_sidecar_schema_and_readonly_contract(tmp_path: Path) -> None:
    db_path = tmp_path / "sidecar_contract.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE bm25_node_stats (
              node_id TEXT PRIMARY KEY,
              updated_at TEXT NOT NULL,
              dl_title INTEGER NOT NULL,
              dl_tags INTEGER NOT NULL,
              dl_body INTEGER NOT NULL,
              dl_meta INTEGER NOT NULL,
              tf_json TEXT NOT NULL
            )
            """
        )
        payload = {
            "tf_title": {"bm25f": 1, "sidecar": 1},
            "tf_tags": {"graphrag": 1},
            "tf_body": {"inverted": 2, "postings": 1},
            "tf_meta": {"document": 1},
            "wikilinks": ["knowledge graph"],
            "tags": ["graphrag"],
        }
        conn.execute(
            "INSERT INTO bm25_node_stats VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("doc-1", "2026-11-15T00:00:00Z", 2, 1, 3, 1, json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()

    ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cols = [
            row[1]
            for row in ro.execute("PRAGMA table_info(bm25_node_stats)").fetchall()
        ]
        assert cols == [
            "node_id",
            "updated_at",
            "dl_title",
            "dl_tags",
            "dl_body",
            "dl_meta",
            "tf_json",
        ]
        row = ro.execute(
            "SELECT dl_title, dl_tags, dl_body, dl_meta, tf_json FROM bm25_node_stats WHERE node_id=?",
            ("doc-1",),
        ).fetchone()
        assert row[:4] == (2, 1, 3, 1)
        loaded = json.loads(row[4])
        assert loaded["tf_title"]["sidecar"] == 1
        assert loaded["wikilinks"] == ["knowledge graph"]
    finally:
        ro.close()
