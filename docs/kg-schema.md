# Knowledge Graph Schema

Current release: **8.4.0 - Action-Aware Brain Chat**.

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

**엔드포인트 룰은 권고 사항이다 (스키마 문서 기준).** 코드에는 엔드포인트 페어
검증기가 존재하지 않는다 — `validate_endpoints` 는 구현된 적이 없으며, 쓰기
경로는 타입 페어를 거부하지 않는다. 현재 쓰기 정규화는 *타입 어휘* 를
강제한다: `_upsert_edge` 가 모든 엣지 타입을 canonical `EdgeType` 값으로
정규화하므로 자유 문자열 타입은 더 이상 생성되지 않는다.

---

## Current Knowledge Graph First 엔티티/관계

8.4.0 은 "모든 데이터 소스가 Knowledge Graph 로 수렴한다"는 원칙을 1급 스키마로
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

구현: `lattice_brain/ingestion.py` (`IngestionPipeline`) 가 단일 진입점이며,
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

마이그레이션은 별도 CLI 없이 **서버 기동 시 자동으로** 일어난다:
`knowledge_graph.KnowledgeGraphStore` 가 열릴 때 v2 스키마를 생성/치유하고
(`kg_schema.KGStoreV2.init_schema` — 추가 컬럼은 `ALTER` 로 in-place 치유,
edges_v2 식별자 변경은 create→copy→swap 으로 재구축), legacy 데이터를
v2 로 백필한다. 기존 `nodes` / `edges` 테이블은 삭제하지 않는다. v4 에서는
`nodes_v2` / `edges_v2` 가 쓰기 마스터이며, legacy 테이블은 이전 import/API
소비자를 위한 compatibility projection 으로 같은 트랜잭션에서 갱신된다.
기존 그래프 데이터가 있는 DB는 전환 전 `backups/*.pre-v2-write-master.*.sqlite`
스냅샷을 한 번 생성하고, `PRAGMA user_version=4` 와 `kg_meta.db_format_version=4`
를 기록한다. 더 높은 포맷의 DB는 fail-closed 로 열리지 않는다.

---

## 임베딩

- 차원: 환경 변수 `LATTICEAI_EMBED_DIM` (기본 `1024`)
- 저장: SQLite `BLOB` 컬럼, `struct.pack('<{n}f', …)` 직렬화
- 검색: `knowledge_graph.KnowledgeGraphStore.vector_search` — 순수 Python
  코사인 (sqlite-vec/ANN 인덱스는 아직 없음). 기본 임베더는 해시 기반
  폴백(`grade='fallback'`)이며, 실제 임베딩 모델은 setup wizard 를 통해
  사용자 동의 하에 프로비저닝한다.
- 키워드 검색: v4 부터 FTS5 trigram 인덱스(`node_fts`) 가 LIKE 스캔을
  대체한다 (한국어 부분 문자열 리콜 유지). FTS5/trigram 이 없는 SQLite
  빌드에서는 LIKE 경로가 그대로 동작하며 `index_status().storage.fts_enabled`
  로 정직하게 보고된다.

---

## 사용 (Python)

`KGStoreV2` 는 **스키마/초기화/통계 전용**이다 — 과거 문서에 있던
`Node`/`Edge` dataclass, `upsert_node`/`upsert_edge`/`neighbors`/
`search_similar` native API 는 제거되었고 존재하지 않는다. 데이터
read/write 는 `knowledge_graph.KnowledgeGraphStore` 가 담당한다:

```python
from kg_schema import KGStoreV2, NodeType, EdgeType
from knowledge_graph import KnowledgeGraphStore

store = KnowledgeGraphStore(db_path, blob_dir)

# 쓰기: 모든 ingest 경로가 내부적으로 _upsert_node/_upsert_edge 를 통과하며,
# 엣지 타입은 canonical EdgeType 으로 정규화된다 (자유 문자열 차단).
store.ingest_message("user", "프로젝트 일정 공유", user_email="me@example.com")

# 읽기: search (FTS5/LIKE), vector_search, graph, traverse
matches = store.search("프로젝트")["matches"]

# v2 통계 (정규화된 타입 분포)
print(KGStoreV2(store.db_path).stats())
```

### v4 컬럼 (T3b/T3c)

- `nodes_v2.workspace_id` — `NULL` = legacy-global (스코프 도입 이전 데이터)
- `nodes_v2.visibility` — 신규 스코프 쓰기는 `workspace`/`private`,
  스코프 없는 쓰기는 `legacy` (기존 공유 데이터를 몰래 private 으로
  만들지 않는다)
- `nodes_v2.superseded_by` — 개정 체인 (`mark_superseded`)
- `edge_occurrences` — 관계의 모든 관측 기록 (observed_at/weight/source)
