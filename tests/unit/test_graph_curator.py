"""Unit tests for latticeai.core.graph_curator (피드백 #4)."""

from latticeai.core.graph_curator import (
    contains_secret,
    mask_secrets,
    extract_topic_candidates,
    cluster_candidates,
    should_promote,
    curate_nodes,
    auto_build_graph_overlay,
    DEFAULT_ALIAS_GROUPS,
    build_alias_index,
)


def test_secret_detection_and_masking():
    text = "set api_key=sk-1234567890abcdefghij1234567890 and continue"
    assert contains_secret(text)
    masked = mask_secrets(text)
    assert "sk-1234567890" not in masked
    assert "[REDACTED]" in masked


def test_alias_index_from_default_groups():
    idx = build_alias_index()
    # 첫 그룹의 별칭들이 모두 첫 항목으로 정규화
    canon = DEFAULT_ALIAS_GROUPS[0][0].lower().strip()
    for alias in DEFAULT_ALIAS_GROUPS[0][1:]:
        assert idx.get(alias.lower().strip()) == canon


def test_extract_topic_candidates_basic():
    docs = [
        {"id": "1", "text": "Lattice AI 그래프 기억 시스템", "kind": "chat"},
        {"id": "2", "text": "Lattice AI 그래프 자동 큐레이션", "kind": "chat"},
        {"id": "3", "text": "Lattice AI 보안 로그", "kind": "file"},
    ]
    cands = extract_topic_candidates(docs, min_score=0.5)
    labels = [c.label for c in cands]
    # 여러 문서에 반복된 토큰이 잡혀야 함
    assert any("lattice" in label for label in labels)


def test_cluster_candidates_merges_aliases():
    docs = [
        {"id": "1", "text": "Lattice AI 그래프", "kind": "chat"},
        {"id": "2", "text": "LatticeAI 그래프 ai", "kind": "chat"},
        {"id": "3", "text": "래티스 AI 그래프 시스템", "kind": "chat"},
    ]
    cands = extract_topic_candidates(docs, min_score=0.0)
    clustered = cluster_candidates(cands)
    # 모두 canonical 'lattice ai' 같은 하나로 묶여야 함
    canonical_labels = {c.label for c in clustered}
    # 적어도 alias 그룹의 어떤 라벨도 별도로 노출되지 않아야 함
    assert not ({"latticeai", "래티스 ai"} & canonical_labels)


def test_should_promote_rejects_secrets():
    docs = [{"id": "1", "text": "api_key=sk-zzzzzzzzzzzzzzzzzzzz1234567890", "kind": "chat"}]
    cands = extract_topic_candidates(docs, min_score=0.0)
    for c in cands:
        if contains_secret(c.label):
            decision = should_promote(c)
            assert decision.promote is False
            assert "secret" in decision.reason


def test_should_promote_rejects_duplicate():
    from latticeai.core.graph_curator import TopicCandidate
    cand = TopicCandidate(label="Lattice AI", score=5.0, sources=["a", "b", "c"])
    decision = should_promote(cand, existing_node_labels={"Lattice AI"})
    assert decision.promote is False
    assert "duplicate" in decision.reason


def test_curate_nodes_sets_visibility():
    import time
    now = time.time()
    nodes = [
        {"id": "1", "label": "Hot", "importance": 5.0, "updated_at": now},
        {"id": "2", "label": "Cold", "importance": 0.2, "updated_at": now - 60 * 60 * 24 * 365},
    ]
    curated = curate_nodes(nodes, max_visible=1, now=now)
    visible = [n for n in curated if n["visible"]]
    assert len(visible) == 1
    assert visible[0]["label"] == "Hot"


def test_auto_build_graph_overlay_skips_secrets_and_limits_new_nodes():
    docs = [
        {"id": str(i), "text": f"중요 주제 {i % 3}번 lattice graph", "kind": "chat"}
        for i in range(10)
    ] + [
        {"id": "secret", "text": "leak api_key=sk-1234567890abcdefghij1234567890", "kind": "chat"},
    ]
    result = auto_build_graph_overlay(docs, max_new_nodes=3)
    assert len(result["promotions"]) <= 3
    for p in result["promotions"]:
        assert "[REDACTED]" not in p["label"]
        assert "sk-" not in p["label"]
