[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../PERFORMANCE.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./PERFORMANCE.md)

# 지식 그래프 및 검색 성능 (Knowledge Graph & Retrieval Performance)

> **상태: 현재 Rust 검색 아키텍처 + 라벨링된 과거 베이스라인.**
> Python 기반의 `scripts/profile_kg.py`(v9.6.0) 및 `scripts/bench_vector_index.py`(v11.1.0) 스크립트는 Knowledge Graph 저장소와 검색 엔진이 Rust(`lattice-core` 및 `lattice-retrieval`)로 이전되면서 퇴역했습니다. 아래의 과거 표들은 버전 세대 간 비교를 위한 참고 베이스라인으로만 보존되며 오늘날 직접 실행 가능한 벤치마크가 **아닙니다**.

## 현재 Rust 검색 아키텍처 (`lattice-retrieval` & `lattice-core`)

### 1. 사전 토큰화된 BM25F 사이드카 (`Bm25SidecarIndex` & `bm25_node_stats`)

- **사전 토큰화된 역색인 포스팅 리스트** (`rust/lattice-retrieval/src/bm25/sidecar.rs`):
  `Bm25SidecarIndex`는 역색인 포스팅 리스트(`term -> Vec<PostingEntry>`), O(1) 문서 빈도 `df(t)` 조회, 사전 집계된 평균 필드 길이(`avgdl_title`, `avgdl_tags`, `avgdl_body`, `avgdl_meta`), 그리고 캐시된 위키링크, 해시태그, 제목, 엣지 인접 맵(`MAX_CACHED_EDGES = 50_000`)을 유지합니다.
- **영속적 필드 통계 하이드레이션 (`bm25_node_stats`)**: `bm25_node_stats` 테이블이 존재하면 사이드카가 사전 토큰화된 노드 필드 통계를 직접 로드하며, `bm25_node_stats`가 없는 읽기 전용 골든 픽스처에서는 스키마를 변경하지 않고 메모리에서 투명하게 통계를 계산합니다.
- **증분 동기화(Incremental sync)**: 쿼리마다 전체 코퍼스를 다시 토큰화하는 대신 `(node_id, updated_at, content_hash)`를 기준으로 변경된 노드만 증분 조정합니다.
- **수명 주기 인식 스코어링(Lifecycle-aware scoring)**: `Bm25SidecarIndex::score`는 내부적으로 `score_with_options(..., include_historical = false)`를 호출하여 대체된(`superseded`) 노드와 사용 중단된(`deprecated`) 노드를 기본 활성 검색에서 배제합니다(`include_historical = true`가 명시적으로 요청된 경우에만 가중치를 낮추어 포함).

### 2. 동시성 사이드카 스냅샷 캐시 (`SidecarCachePool`)

`rust/lattice-retrieval/src/bm25/sidecar_cache.rs`에 구현되어 있습니다.

- **공유 읽기 동시성**: 각 캐시 엔트리는 `RwLock<Arc<Bm25SidecarIndex>>`를 저장합니다. 동시 쿼리 및 GraphRAG 리더들은 `Arc<Bm25SidecarIndex>` 스냅샷을 복제(clone)하여 순회나 점수 계산 동안 쓰기 락을 잡지 않고 병렬로 실행됩니다.
- **용량 제한 및 LRU 축출(Eviction)**:
  - 최대 캐시 엔트리 수: `MAX_CACHED_SIDECARS = 64`
  - 기본 최대 바이트 용량: `DEFAULT_MAX_CACHE_BYTES = 256 * 1024 * 1024` (256 MiB, `LATTICEAI_SIDECAR_CACHE_MAX_BYTES`로 재정의 가능)
  - 축출 로직은 엔트리 개수와 추정 메모리 사용량을 모두 고려하며 `SidecarCacheStats` 카운터(`cache_hits`, `cache_misses`, `incremental_updates`, `tokenized_nodes`, `sidecar_table_hits`, `subword_computed_nodes`)를 추적합니다.

### 3. GraphRAG 1-Hop 및 2-Hop 구조적 확장 (`graph_rag.rs`)

`rust/lattice-retrieval/src/bm25/graph_rag.rs`에 구현되어 있습니다.

- **스냅샷 안전 확장**: `expand_knowledge_graph_with_snapshot`은 불변 `Bm25SidecarIndex` 스냅샷에서 직접 엣지 인접성을 읽고, 캐시되지 않은 노드 행은 SQL 수준의 워크스페이스 스코프 및 `KnowledgeLifecycleStatus` 필터를 통해 가져옵니다.
- **기본 순회 파라미터** (`GraphRagConfig::default()`):
  - `max_hops = 2`
  - `max_seeds = 12`
  - `max_neighbors = 40`
  - `hop1_decay = 0.68`
  - `hop2_decay = 0.38`
  - `include_historical = false`
- **관계 가중치**: `PART_OF` (1.00), `RELATES_TO` (0.95), `DEPENDS_ON` (0.92), `USES` / `IMPLEMENTS` (0.90), `MENTIONS` / `REFERENCES` (0.85), `DERIVED_FROM` / `SUMMARIZES` (0.82), `HAS_TAG` / `WIKILINK` / `SHARED_CONCEPT` (0.80), 기본 폴백 (0.68).

### 4. 벡터 검색 및 `EmbeddingIndexContract` (`vector.rs` & `embeddings.rs`)

- **자동 HNSW 사이드카 임계값 (`HNSW_AUTO_MIN_ROWS = 512`)**: Brain에 최소 512개 이상의 벡터가 있고 워커 사이드카 오리진이 바인딩된 경우, 기본 벡터 검색은 HNSW 사이드카(`k * 8`개 후보, 최대 `200`개)를 먼저 조회한 뒤 해당 행들을 코사인 유사도로 정밀 재채점(`hnsw+rescore`)하며, 사이드카 미스 시에는 범위가 제한된 정밀 스캔(`DEFAULT_VECTOR_MAX_CANDIDATES = 10_000`, 상한 `500_000`)으로 안전하게 폴백합니다.
- **계약으로 보호되는 검색**: `EmbeddingIndexContract`(`lattice-core::embeddings`와 `latticeai.core.embedding_providers` 간에 필드가 일대일로 공유됨)가 `provider`, `model_id`, `dimension`, `normalization`, `passage_mode`, `query_mode`, `index_version`의 일치 여부를 강제하여, 호환되지 않는 벡터 공간을 섞는 대신 `IngestStatus::ReindexRequired`를 보고합니다.

### 활성 성능 및 계약 검증

v12.7+ Rust 사이드카에 대한 별도의 엔드투엔드 처리량/지연 시간 스윕은 이 문서에 새로 캡처되어 있지 않으므로 아래의 과거 v9.6.0/v11.1.0 Python 수치를 Rust `SidecarCachePool` 경로로 외삽하지 마십시오. 활성 BM25F, GraphRAG 및 벡터 계약 동작은 다음 명령어로 검증할 수 있습니다.

```bash
cargo test --manifest-path rust/Cargo.toml -p lattice-retrieval --tests
```

---

## 과거 참고 베이스라인 (퇴역한 Python 하니스)

> **과거 시점의 스냅샷 (현재 실행 불가).** 세대 간 비교 맥락을 위해서만 보존됩니다. `scripts/profile_kg.py`(v9.6.0)와 `scripts/bench_vector_index.py`(v11.1.0)는 결정론적 해시 임베더와 합성 코퍼스를 사용하여 퇴역한 Python `lattice_brain` 스토어를 측정했습니다.

### 레거시 v9.6.0 Python KG 프로파일 (`500` 및 `5000` 합성 소스)

Apple Silicon(darwin), FTS5 트라이그램이 활성화된 SQLite, `tracemalloc` 활성화(Python 할당 시간을 약 2배 부풀림) 환경에서 측정된 결과입니다.

- **500개 소스 (2,019개 노드 / 6,546개 엣지, 21.2 MB DB)**:
  `ingest_source` p50 `6.9 ms` / p95 `17.7 ms` (초당 ~122개 항목);
  `search` p50 `1.2 ms` / p95 `2.5 ms`;
  `traverse(depth=2)` p50 `4.8 ms` / p95 `5.2 ms`;
  `vector_search` (brute) p50 `287 ms` / p95 `294 ms`.
- **5000개 소스 (18,426개 노드 / 62,534개 엣지, 198.8 MB DB)**:
  `ingest_source` p50 `20.1 ms` / p95 `54.6 ms` (초당 ~42개 항목);
  `search` p50 `4.3 ms` / p95 `19.8 ms`;
  `traverse(depth=2)` p50 `12.2 ms` / p95 `15.8 ms`;
  `vector_search` (brute) p50 `1732 ms` / p95 `1774 ms`.

### 레거시 v11.1.0 벡터 백엔드 비교 (2026-08-10, 1만 및 5만 합성 벡터)

macOS(arm64), Python 3.14.5, SQLite 3.53.1, `hnswlib` 0.8.0, 무제한 후보, 결정론적 해시 임베더 환경에서 측정된 결과입니다.

| 코퍼스 | 백엔드 | p50 ms | p95 ms | First ms | Recall@10 | Hybrid p50 ms |
|---|---|---:|---:|---:|---:|---:|
| 10,000개 벡터 (30개 쿼리) | `brute` | 293.25 | 299.31 | 293.91 | 1.000 | 299.17 |
| 10,000개 벡터 (30개 쿼리) | `quantized` | 640.58 | 650.15 | 636.87 | 0.987 | 653.33 |
| 10,000개 벡터 (30개 쿼리) | `hnsw` | 7.01 | 7.50 | 799.95 | 0.953 | 10.07 |
| 50,000개 벡터 (15개 쿼리) | `brute` | 1515.31 | 1522.94 | 1520.89 | 1.000 | 1514.75 |
| 50,000개 벡터 (15개 쿼리) | `quantized` | 3254.76 | 3275.69 | 3253.54 | 0.967 | 3441.13 |
| 50,000개 벡터 (15개 쿼리) | `hnsw` | 35.58 | 38.19 | 6114.60 | 0.987 | 43.90 |

2026-08-10 측정의 핵심 시사점: 정밀 전수 스캔(`brute`)은 인덱스 크기에 선형 비례하여 증가하고, `quantized`는 배치 정밀 스캔 대비 RAM 이점이 없으면서 지연 시간만 약 2.2배 증가시켰으며, `hnsw`는 5만 행에서도 하이브리드 지연 시간을 50 ms 미만으로 유지했습니다(이는 이후 Rust의 `HNSW_AUTO_MIN_ROWS = 512` + `hnsw+rescore` 아키텍처 도입의 근거가 되었습니다).
