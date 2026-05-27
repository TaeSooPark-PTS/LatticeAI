"""Tests for the document generation pipeline: intent detection, context builder, hybrid retrieval."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from knowledge_graph import KnowledgeGraphStore, _extract_concepts, _extract_concepts_rules, _extract_triples, _extract_triples_rules
from latticeai.core.document_generator import detect_document_intent, DocumentGenerationSession, build_document_system_prompt
from latticeai.core.context_builder import retrieve_context_for_generation, format_sources_footnote


def _store(tmp_path: Path) -> KnowledgeGraphStore:
    return KnowledgeGraphStore(tmp_path / "kg.sqlite", tmp_path / "blobs")


class TestIntentDetection:
    def test_korean_report_request(self):
        assert detect_document_intent("Q3 마케팅 전략 보고서 작성해줘") is True

    def test_korean_plan_request(self):
        assert detect_document_intent("우리 팀 신규 프로젝트 계획서 만들어줄래?") is True

    def test_english_report_request(self):
        assert detect_document_intent("Write me a quarterly sales report") is True

    def test_english_draft_request(self):
        assert detect_document_intent("Draft a project proposal for the new feature") is True

    def test_simple_question_not_document(self):
        assert detect_document_intent("오늘 날씨 어때?") is False

    def test_short_message_not_document(self):
        assert detect_document_intent("hi") is False

    def test_empty_message(self):
        assert detect_document_intent("") is False

    def test_code_question_not_document(self):
        assert detect_document_intent("이 함수가 어떻게 동작해?") is False

    def test_korean_strategy_document(self):
        assert detect_document_intent("2026년 비즈니스 전략서 생성해줘") is True

    def test_manual_request(self):
        assert detect_document_intent("사용자 매뉴얼 작성해줘") is True


class TestDocumentGenerationSession:
    def test_initial_state(self):
        session = DocumentGenerationSession()
        assert session.has_previous is False

    def test_update_and_followup(self):
        session = DocumentGenerationSession()
        session.update("context1", "document1", "conv1")
        assert session.has_previous is True
        prompt = session.get_system_prompt("new_context")
        assert "이전 문서" in prompt or "previous" in prompt.lower()

    def test_clear(self):
        session = DocumentGenerationSession()
        session.update("ctx", "doc", "conv")
        session.clear()
        assert session.has_previous is False

    def test_system_prompt_without_context(self):
        prompt = build_document_system_prompt("")
        assert "지식 기반" in prompt

    def test_system_prompt_with_context(self):
        prompt = build_document_system_prompt("관련 문서: AI 전략.pdf")
        assert "AI 전략" in prompt


class TestConceptExtraction:
    def test_rule_based_extracts_concepts(self):
        text = "Lattice AI uses MLX for local inference. FastAPI serves the backend."
        concepts = _extract_concepts_rules(text, limit=5)
        assert len(concepts) > 0
        concept_lower = [c.lower() for c in concepts]
        assert any("lattice" in c for c in concept_lower)

    def test_extract_concepts_delegates_to_rules_when_no_llm(self):
        text = "Python and FastAPI are used in this project."
        concepts = _extract_concepts(text, limit=5)
        assert len(concepts) > 0

    def test_korean_concepts(self):
        text = "멀티모달AI 에이전트가 그래프RAG를 활용합니다."
        concepts = _extract_concepts_rules(text, limit=5)
        assert len(concepts) > 0


class TestTripleExtraction:
    def test_rule_based_extracts_triples(self):
        text = "FastAPI uses Pydantic for data validation. Lattice AI depends on MLX for inference."
        concepts = ["FastAPI", "Pydantic", "Lattice AI", "MLX"]
        triples = _extract_triples_rules(text, concepts, limit=5)
        assert len(triples) > 0
        assert all("subject" in t and "object" in t for t in triples)

    def test_empty_concepts(self):
        triples = _extract_triples_rules("some text", [], limit=5)
        assert triples == []

    def test_single_concept(self):
        triples = _extract_triples_rules("FastAPI is great", ["FastAPI"], limit=5)
        assert triples == []


class TestHybridRetrieval:
    def test_search_for_document_generation_empty_query(self, tmp_path):
        store = _store(tmp_path)
        results = store.search_for_document_generation("", limit=10)
        assert results == []

    def test_search_for_document_generation_with_data(self, tmp_path):
        store = _store(tmp_path)
        store.ingest_message(
            "user", "Q3 마케팅 전략은 소셜 미디어 집중이 핵심이다.",
            conversation_id="conv1",
        )
        store.ingest_message(
            "assistant", "마케팅 전략 보고서를 작성하겠습니다. 소셜 미디어 중심 전략을 다루겠습니다.",
            conversation_id="conv1",
        )
        results = store.search_for_document_generation("마케팅 전략", limit=10)
        assert len(results) > 0
        assert all("hybrid_score" in r for r in results)
        assert all("scores" in r for r in results)

    def test_multi_hop_context(self, tmp_path):
        store = _store(tmp_path)
        store.ingest_message("user", "FastAPI와 MLX를 사용해서 AI 서버를 만들자", conversation_id="conv1")
        graph = store.graph()
        node_ids = [n["id"] for n in graph["nodes"][:3]]
        if node_ids:
            result = store.multi_hop_context(node_ids, max_hops=2)
            assert "nodes" in result
            assert "edges" in result


class TestContextBuilder:
    def test_empty_query(self):
        result = retrieve_context_for_generation(None, "")
        assert result["context_markdown"] == ""

    def test_no_kg_store(self):
        result = retrieve_context_for_generation(None, "write a report")
        assert result["sources"] == []

    def test_with_populated_store(self, tmp_path):
        store = _store(tmp_path)
        store.ingest_message(
            "user", "2026년 AI 기술 동향: MLX 로컬 추론이 주목받고 있다.",
            conversation_id="conv1",
        )
        store.ingest_message(
            "user", "Q2 프로젝트 계획: Lattice AI를 오픈소스로 출시한다.",
            conversation_id="conv2",
        )
        result = retrieve_context_for_generation(store, "AI 기술 동향 보고서", max_results=5)
        assert result["query"] == "AI 기술 동향 보고서"
        assert len(result["context_markdown"]) > 0

    def test_format_sources_footnote(self):
        sources = [
            {"id": "file:abc", "type": "Document", "title": "전략서.pdf", "source": "docs/전략서.pdf"},
            {"id": "chat:xyz", "type": "Chat", "title": "AI 논의", "source": "conv123"},
        ]
        footnote = format_sources_footnote(sources)
        assert "참조된 지식 그래프 노드" in footnote
        assert "전략서.pdf" in footnote

    def test_empty_sources_footnote(self):
        assert format_sources_footnote([]) == ""


class TestKGSchemaV2Enhancements:
    def test_document_node_type_exists(self):
        from kg_schema import NodeType
        assert hasattr(NodeType, "DOCUMENT")
        assert NodeType.DOCUMENT.value == "DOCUMENT"

    def test_new_edge_types_exist(self):
        from kg_schema import EdgeType
        for name in ("USED_IN", "INSPIRED_BY", "CONTRADICTS", "EVOLVES_FROM"):
            assert hasattr(EdgeType, name), f"EdgeType.{name} missing"

    def test_node_has_document_fields(self):
        from kg_schema import Node, NodeType
        node = Node(
            type=NodeType.DOCUMENT,
            label="Q3 보고서",
            style="formal",
            tone="professional",
            importance_score=0.85,
        )
        assert node.style == "formal"
        assert node.tone == "professional"
        assert node.importance_score == 0.85

    def test_v2_store_upsert_document_node(self, tmp_path):
        from kg_schema import KGStoreV2, Node, NodeType
        store = KGStoreV2(str(tmp_path / "v2.db"))
        store.init_schema()
        node = Node(
            type=NodeType.DOCUMENT,
            label="테스트 문서",
            style="casual",
            tone="friendly",
            importance_score=0.7,
        )
        nid = store.upsert_node(node)
        retrieved = store.get_node(nid)
        assert retrieved is not None
        assert retrieved.type == NodeType.DOCUMENT
        assert retrieved.style == "casual"
        assert retrieved.tone == "friendly"
        assert retrieved.importance_score >= 0.7
