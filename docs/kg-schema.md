# Knowledge Graph — v2 스키마

명세 출처: `lattice_ai_full_spec.pptx` 슬라이드 20·21·22
구현: `kg_schema.py`

---

## 한 줄 요약

> **점(노드)은 워크스페이스의 모든 *명사*. 선(엣지)은 모든 *동사*.**
> 모든 노드는 임베딩 벡터를 가지고, 모든 엣지는 (방향 · 타입 · 가중치 · 신뢰도 · 근거)를 가진다.

---

## 점 (Node)

```
Node {
  id          string          // ULID, 영속, 전역 유일 ("node:01HX…")
  type        NodeType        // 아래 10가지 중 하나
  label       string          // 사람이 읽는 짧은 이름 (≤240자)
  attrs       object          // 타입별 구조화 메타데이터
  embedding   float[1024]?    // 의미 벡터 (옵셔널, 권장)
  owner_id    string?         // 소유 사용자
  visibility  Visibility      // private | internal | shared | public
  created_at  ISO8601 UTC
  updated_at  ISO8601 UTC
}
```

### 노드 타입 카탈로그

| 타입 | 의미 | 대표 `attrs` |
|------|------|--------------|
| `CONVERSATION` | 대화 세션 전체 | `started_at`, `model_id`, `mode` |
| `MESSAGE` | 단일 발화 | `role`, `tokens`, `parent_id` |
| `FILE` | 업로드/연결된 파일 | `mime`, `sizeBytes`, `pageCount`, `lang` |
| `CHUNK` | 파일의 분할 청크 | `parent_file`, `offset`, `length` |
| `CODE_SYMBOL` | 함수·클래스·모듈 | `path`, `lang`, `signature` |
| `CONCEPT` | 추출된 개념 / 태그 | `aliases[]`, `extractor` |
| `PERSON` | 사용자·협업자 | `email`, `role` |
| `MODEL` | 로컬/원격 LLM | `provider`, `runtime`, `quantization` |
| `TOOL` | MCP 서버·외부 도구 | `scope`, `version`, `capabilities[]` |
| `PROJECT` | 주제별 작업 공간 | `description`, `members[]` |

---

## 선 (Edge)

```
Edge {
  id           string          // ULID ("edge:01HX…")
  source       string          // 출발 노드 id
  target       string          // 도착 노드 id (방향 있음)
  type         EdgeType        // 아래 12가지 중 하나
  weight       float [0..1]    // 관계의 ‘강도’
  confidence   float [0..1]    // 추출/추론의 ‘신뢰도’
  evidence     string[]        // 근거 (메시지/청크 ID 리스트)
  created_by   string          // extractor:llm-gemma-4-12b | rule:regex | user
  created_at   ISO8601 UTC
}
```

### 엣지 타입 카탈로그

| 타입 | 허용 source → target | 의미 |
|------|---------------------|------|
| `CONTAINS`      | `FILE → CHUNK` | 파일이 청크를 포함 |
| `MENTIONS`      | `MESSAGE`·`FILE`·`CHUNK` → `CONCEPT`·`PERSON`·`MODEL`·`TOOL` | 언급/등장 |
| `REFERENCES`    | `FILE`·`MESSAGE`·`CHUNK` → 동일 | 명시적 참조/링크 |
| `REPLIES_TO`    | `MESSAGE → MESSAGE` | 답글 |
| `AUTHORED_BY`   | `FILE`·`MESSAGE`·`CONVERSATION` → `PERSON` | 작성자 |
| `USES`          | `PROJECT`·`CONVERSATION` → `TOOL`·`MODEL` | 도구/모델 사용 |
| `DERIVED_FROM`  | `CHUNK`·`FILE` → 동일 | 요약·재가공 출처 |
| `SIMILAR_TO`    | ANY ↔ ANY | 코사인 유사도 기반 (자기 자신 허용) |
| `DEPENDS_ON`    | `CODE_SYMBOL → CODE_SYMBOL` | 코드 의존 관계 |
| `TAGGED_AS`     | ANY → `CONCEPT` | 태깅 |
| `VERSION_OF`    | `FILE → FILE` | 버전 히스토리 |
| `GRANTS_ACCESS` | `PERSON → FILE`·`CONVERSATION`·`PROJECT` | 접근 권한 부여 |

**엔드포인트 룰은 코드에서 강제된다** (`validate_endpoints` in `kg_schema.py`).
잘못된 페어(예: `FILE → FILE` 에 `REPLIES_TO`)는 `upsert_edge` 가 거부한다.

---

## v3.6.0 — Knowledge Graph First 엔티티/관계

v3.6.0 은 "모든 데이터 소스가 Knowledge Graph 로 수렴한다"는 원칙을 1급 스키마로
승격한다. 아래 타입은 **추가형(additive)**이다 — 기존 enum/legacy 매핑을 깨지 않고
`from_legacy` 가 무손실로 정규화하며, 알 수 없는 타입은 여전히 `CONCEPT`/`MENTIONS` 로
폴백한다. 스키마는 **확장 가능**하게 유지한다: 새 도메인 엔티티는 enum 멤버 1개 +
`_LEGACY_NODE_MAP`/`_LEGACY_EDGE_MAP` 별칭만 추가하면 된다.

### 추가 노드 타입

| 타입 | 의미 | 대표 `attrs` / 출처 |
|------|------|--------------------|
| `SOURCE` | 수집 출처(파일/URL/브라우저 탭/git 등)의 **출처 노드** | `source_type`, `source_uri`, `content_hash`, `captured_at` |
| `REPOSITORY` | git 저장소 | `remote`, `branch`, `head` |
| `MEETING` | 회의 / 미팅 | `started_at`, `attendees[]` |
| `ORGANIZATION` | 조직 / 회사 / 팀 | `domain`, `members[]` |
| `WORKFLOW` | 워크플로우 정의/실행 | `workflow_id`, `status` |
| `AGENT` | 에이전트(역할/실행 주체) | `role`, `model_id` |

### 추가 엣지 타입

| 타입 | 허용 source → target | 의미 |
|------|---------------------|------|
| `INDEXED_FROM` | ANY → `SOURCE` | 이 노드가 **어떤 출처에서 색인**됐는가 (provenance) |
| `MODIFIED_BY` | ANY → `PERSON` | 마지막 수정자 |
| `BELONGS_TO_PROJECT` | ANY → `PROJECT` | 프로젝트 귀속 |
| `PART_OF` | ANY → ANY | 구성요소 관계 |
| `DISCUSSED_IN` | `CONCEPT`·`DECISION` → `MEETING`·`CHAT` | 어디에서 논의됨 |
| `DECIDED_BY` | `DECISION` → `PERSON` | 결정 주체 |
| `GENERATED_BY` | ANY → `AGENT`·`MODEL`·`WORKFLOW` | 생성 주체 |
| `USED_BY_AGENT` | ANY → `AGENT` | 에이전트가 사용함 |

### 통합 수집 형태 (Unified Ingestion)

모든 출처는 동일한 형태로 그래프에 들어온다:

```
SOURCE ──INDEXED_FROM◄── Document/File ──CONTAINS──► Chunk[]
   ▲                          │
   │                          └──(언급/포함)──► Concept / Task / Decision …
provenance(source_type, source_uri, content_hash, captured_at, modified_at,
           owner, workspace_id, permissions, pipeline, embedded, linked)
```

- **콘텐츠 노드**(Document/File/web 노드)는 `content_hash` 로 멱등(idempotent) 처리된다 —
  같은 콘텐츠를 다시 수집하면 새 노드를 만들지 않고 갱신/링크한다.
- 모든 콘텐츠 노드는 `SOURCE` 노드로 `INDEXED_FROM` 엣지를 가져 **출처를 항상 설명 가능**하다.
- provenance 는 노드 `metadata.provenance` 에 임베드되며, 동시에 감사 가능한
  `ingestion_provenance` 테이블에 기록된다 (`KnowledgeGraphStore.get_provenance(node_id)`).

구현: `latticeai/services/ingestion.py` (`IngestionPipeline`) 가 단일 진입점이며,
파일/로컬폴더/URL/브라우저 탭/텍스트를 모두 이 형태로 정규화한다.

---

## 예시 (PPT 슬라이드 22 와 동일)

```json
{
  "node": {
    "id":         "node:01HX7K…",
    "type":       "FILE",
    "label":      "LatticeAI_기획서.pdf",
    "embedding":  [0.014, -0.231, "…", 0.082],
    "attrs":      { "mime":"application/pdf",
                    "pageCount":24, "lang":"ko" },
    "owner_id":   "user_seoljun",
    "visibility": "private",
    "created_at": "2026-05-20T05:30:00Z"
  },
  "edge": {
    "id":         "edge:01HX7M…",
    "source":     "node:01HX7K…",        // FILE
    "target":     "node:01HX5A…",        // CONCEPT  'MCP'
    "type":       "MENTIONS",
    "weight":     0.82,
    "confidence": 0.91,
    "evidence":   ["chunk:01HX7K…#p3", "chunk:01HX7K…#p11"],
    "created_by": "extractor:llm-gemma-4-12b"
  }
}
```

---

## 마이그레이션 (legacy → v2)

기존 `knowledge_graph.py` 가 만든 `nodes` / `edges` 테이블은 자유 문자열 타입을
사용해 왔다. `kg_schema.py` 는 매핑 표를 가지고 있어 정식 enum 으로 변환한다.

| legacy 타입 (한글 동사) | → v2 `EdgeType` |
|------------------------|------------------|
| `언급함`, `설명함`, `관련됨` | `MENTIONS` |
| `포함함` | `CONTAINS` |
| `해결함`, `연결함`, `발생함` | `REFERENCES` |
| `의존함` | `DEPENDS_ON` |
| `비교함` | `SIMILAR_TO` |
| `사용함`, `지원함` | `USES` |
| `확장함` | `DERIVED_FROM` |
| `생성함` | `AUTHORED_BY` |
| `대체함` | `VERSION_OF` |

| legacy 타입 (노드, 자유 문자열) | → v2 `NodeType` |
|-------------------------------|------------------|
| `Code` | `CODE_SYMBOL` |
| `Concept`, `Feature`, `Error`, `Tag` | `CONCEPT` |
| `Person`, `User` | `PERSON` |
| `File`, `Document`, `Page`, `Sheet`, `Slide` | `FILE` / `CHUNK` |
| `Message`, `AIResponse` | `MESSAGE` |
| `Model` | `MODEL` |
| `Tool`, `MCP` | `TOOL` |
| `Project`, `Workspace` | `PROJECT` |

### 실행

```bash
# 1) 현재 DB 의 어떤 row 가 어떻게 변환될지만 보기
python3 kg_schema.py migrate ~/.ltcai/knowledge_graph.db --dry-run

# 2) 실제 마이그레이션 (v2 테이블에 복사. 기존 테이블은 보존)
python3 kg_schema.py migrate ~/.ltcai/knowledge_graph.db

# 3) 결과 확인
python3 kg_schema.py stats ~/.ltcai/knowledge_graph.db
```

마이그레이션은 **기존 `nodes` / `edges` 를 건드리지 않는다.** 신규 `nodes_v2` / `edges_v2`
테이블에 복사할 뿐이다. 새 코드가 안정화되면 다음 메이저 릴리스에서 legacy 테이블을
DROP 한다.

---

## 임베딩

- 차원: 환경 변수 `LATTICEAI_EMBED_DIM` (기본 `1024`)
- 저장: SQLite `BLOB` 컬럼, `struct.pack('<{n}f', …)` 직렬화
- 검색: `KGStoreV2.search_similar(vec, top_k=8)` — sqlite-vec 가 없는 환경에서도
  순수 Python 코사인으로 동작. sqlite-vec 가 설치되면 인덱스 자동 활용 (추후).

임베딩 모델은 LLM 라우터(`llm_router.py`) 가 결정한다 — 기본 `sentence-transformers/all-MiniLM-L12-v2`
(384-d, dim 변경시 `LATTICEAI_EMBED_DIM` 도 함께 설정).

---

## 사용 (Python)

```python
from kg_schema import KGStoreV2, Node, Edge, NodeType, EdgeType, Visibility

store = KGStoreV2("/Users/me/.ltcai/kg_v2.db")
store.init_schema()

# 노드 만들기
file_node = Node(
    type=NodeType.FILE,
    label="LatticeAI_기획서.pdf",
    attrs={"mime": "application/pdf", "pageCount": 24, "lang": "ko"},
    owner_id="user_seoljun",
    visibility=Visibility.PRIVATE,
)
store.upsert_node(file_node)

# 관계 만들기
store.upsert_edge(Edge(
    source=file_node.id,
    target=concept_node.id,
    type=EdgeType.MENTIONS,
    weight=0.82, confidence=0.91,
    evidence=["chunk:01HX7K…#p3"],
    created_by="extractor:llm-gemma-4-12b",
))

# 이웃 탐색
for edge, other in store.neighbors(file_node.id, edge_type=EdgeType.MENTIONS):
    print(f"-[{edge.type.value}]-> {other.label}")

# 의미 검색
for node, score in store.search_similar(query_embedding, top_k=8):
    print(f"{score:+.3f}  {node.type.value:>12}  {node.label}")
```
