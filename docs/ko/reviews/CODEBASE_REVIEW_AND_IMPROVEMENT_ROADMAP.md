[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../reviews/CODEBASE_REVIEW_AND_IMPROVEMENT_ROADMAP.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./CODEBASE_REVIEW_AND_IMPROVEMENT_ROADMAP.md)

# Lattice AI 전체 소스코드 리뷰 및 개선 로드맵

- **검토 기준일:** 2026-11-18
- **저장소:** https://github.com/TaeSooPark-PTS/LatticeAI
- **검토 대상:** Rust Core / Retrieval / Knowledge Graph / Agent / Ingestion / Python Worker / React Frontend / Tauri / Security / Tests / CI/CD
- **문서 목적:** 코드 개선 작업의 우선순위와 검증 기준을 정리한 실행용 리뷰 문서

> **검토 범위 안내**
>
> 이 문서는 GitHub에서 확인한 주요 소스 파일과 구조를 바탕으로 작성한 정적 코드 리뷰입니다. 저장소의 모든 파일을 한 줄씩 전수 검사하거나, 실제 Mac에서 전체 빌드·테스트·성능 측정을 수행한 결과는 아닙니다. 따라서 점수는 정량 벤치마크가 아닌 코드 구조와 구현 완성도에 대한 정성 평가입니다. 실제 결함 여부와 영향도는 로컬 환경에서 재현 및 테스트해야 합니다.

---

## 1. 종합 평가

Lattice AI는 단순한 RAG 애플리케이션을 넘어 Digital Brain 플랫폼의 기반을 갖춘 프로젝트입니다. Rust 중심 데이터 소유권, Hybrid Retrieval, Knowledge Graph, Provenance, Agent 실행 통제, Local-First 보안 경계가 핵심 강점입니다.

현재 가장 중요한 과제는 기능 수를 늘리는 것이 아니라, 축적된 지식을 의미적으로 통합하고 실제 질문에 대해 정확한 답변을 생성하는 Brain Engine의 품질을 증명하는 것입니다.

### 1.1 정성 평가

| 평가 영역 | 평가 | 판단 |
|---|---:|---|
| 전체 아키텍처 | 8.5/10 | 구조적 기반 우수 |
| Rust Core | 8.5/10 | 데이터 소유권 명확 |
| RAG / Retrieval | 8.0/10 | 검색 구조 발전 |
| Knowledge Graph | 8.0/10 | 관계 추론 기반 확보 |
| Agent 시스템 | 8.0/10 | 권한 및 실행 통제 우수 |
| 데이터 수집 / 인덱싱 | 7.5/10 | 자동화 및 멀티모달 보완 필요 |
| Python Worker | 8.0/10 | 계산 역할 분리 양호 |
| Frontend / UX | 7.5/10 | 기능은 풍부하나 경험 통합 필요 |
| 보안 / 개인정보 | 8.0/10 | 좋은 경계 설계, 추가 검증 필요 |
| 테스트 / CI/CD | 8.0/10 | 체계적이나 검증 범위 불균형 |

**주의:** 위 점수는 실행 성능, 테스트 통과율, 실제 AI 답변 품질을 측정한 결과가 아닙니다.

### 1.2 핵심 결론

- **강점:** 아키텍처, 데이터 소유권, 검색 엔진, 보안 경계
- **보완점:** 실제 AI 품질 검증, 데이터 이해, 자동화, 운영 효율
- **최우선 과제:** 실제 RAG 평가 데이터셋, Entity Resolution, Temporal Knowledge, 근거 검증

---

## 2. 전체 아키텍처 리뷰

확인한 주요 구성은 다음과 같습니다.

| 구성요소 | 역할 |
|---|---|
| React / Vite | 사용자 인터페이스 |
| Tauri | 데스크톱 실행 환경 |
| `lattice-host` | 제품 진입점 및 API 게이트웨이 |
| `lattice-core` | SQLite, GraphWriter, 데이터 소유권 |
| `lattice-retrieval` | RAG, 검색, Knowledge Graph |
| `lattice-ingest` | 파일 수집, 파싱, 청킹 |
| `lattice-agent` | 에이전트 실행 및 보안 정책 |
| `lattice-chat` | 대화 처리와 Cloud 연동 |
| `lattice-jobs` | 인덱싱 작업 관리 |
| Python Worker | LLM, 임베딩, 문서 처리 |

Rust workspace는 9개 크레이트로 구성된 것으로 확인했습니다.

### 2.1 잘한 점

1. **One Door 아키텍처**
   - `rust/lattice-host/src/gateway/onedoor.rs`
   - Store, GraphWriter, AuthState, WorkspaceResolver 등 주요 상태를 공유하는 구조입니다.
   - 중복 DB 연결과 모듈별 상태 불일치를 줄이고 데이터 쓰기 경로를 통일하는 데 유리합니다.

2. **Rust 중심 데이터 소유권**
   - Python은 계산을 수행하고 Rust가 데이터 영속성을 책임지는 방향은 장기 안정성에 유리합니다.
   - ML 생태계가 강한 Python을 추론 및 처리에 활용하면서 핵심 데이터 무결성은 Rust가 담당할 수 있습니다.

3. **모듈별 책임 분리**
   - `lattice-agent`, `lattice-retrieval`, `lattice-ingest` 등의 분리는 기능 확장과 유지보수에 유리합니다.

### 2.2 아쉬운 점 및 개선

1. **도메인 간 결합**
   - `lattice-host`가 여러 모듈의 어댑터 역할을 직접 수행하면 장기적으로 조립 코드와 비즈니스 로직이 뒤섞일 수 있습니다.
   - 개선: 도메인 간 직접 참조 최소화, Port/Adapter 계약 명확화, 이벤트 기반 전달 확대, Host의 조립 책임과 비즈니스 로직 분리.

2. **Python Worker 장애 영향**
   - LLM 추론, MLX, 임베딩, 문서 파싱 등이 Python Worker에 남아 있으므로 Worker 장애가 핵심 기능에 영향을 줄 수 있습니다.
   - 개선: 장애 격리, 요청 타임아웃, 재시작 정책, 모델 상태 복구, 작업 재시도 및 멱등성 보장.

3. **코드 주석과 실제 구조 불일치**
   - 확인한 `rust/lattice-core/src/db.rs` 상단 주석은 Knowledge Graph의 단일 쓰기 주체를 Python Worker라고 설명하지만, 실제 `GraphWriter` 및 `onedoor.rs`는 Rust가 쓰기를 소유하는 구조를 나타냅니다.
   - 개선: 오래된 주석과 모듈 설명을 현행 아키텍처에 맞추고, 구조적 계약을 검사하는 정적 규칙을 검토합니다.

---

## 3. Rust Core 및 데이터 저장소

검토한 파일:

- `rust/lattice-core/src/db.rs`
- `rust/lattice-core/src/graph_write/mod.rs`
- `rust/lattice-core/src/graph_write/ingest.rs`
- `rust/lattice-core/src/graph_write/provenance.rs`
- `rust/lattice-core/src/graph_write/vectors.rs`
- `rust/lattice-core/src/embedding_contract.rs`

### 3.1 잘한 점

1. **GraphWriter 단일 쓰기 구조**
   - 다양한 데이터가 동일한 쓰기 엔진을 거치도록 하는 방향은 SQLite 환경에서 적절합니다.
   - 데이터 변경 경로를 통일하면 트랜잭션 및 무결성 관리가 쉬워집니다.

2. **Provenance 관리**
   - `provenance.rs`에서 정보의 출처와 생성 경로를 추적할 수 있는 기반을 제공합니다.
   - 어떤 파일·콘텐츠·파이프라인에서 지식이 생성되었는지 기록하는 것은 Brain의 신뢰성에 중요합니다.

3. **Embedding Contract**
   - 모델 ID, 차원, 정규화, passage/query 모드 등을 명시적으로 관리하는 접근은 모델 교체와 재색인에 중요합니다.
   - 서로 다른 임베딩 공간의 벡터를 무조건 혼합하지 않도록 해야 합니다.

### 3.2 아쉬운 점 및 개선

1. **데이터 무결성 시나리오 강화**
   - 문서 저장 후 임베딩 실패
   - 임베딩 생성 후 벡터 저장 실패
   - 파일 변경 중 인덱싱 요청
   - 중복·동시 수집
   - 복원 중 검색 요청
   - 개선: 각 실패 지점에서 원자성, 재시도, 보상 작업, 멱등성을 검증하는 통합 테스트를 추가합니다.

2. **SQLite 확장성 측정**
   - SQLite를 즉시 교체할 필요는 없습니다.
   - 다음 규모별로 검색 지연, 쓰기 처리량, 메모리, WAL 증가량, 백업·복원 시간을 측정합니다.

| 데이터 규모 | 검증 항목 |
|---|---|
| 1만 노드 | 검색 지연 및 인덱싱 |
| 10만 노드 | 메모리 및 캐시 |
| 50만 노드 | 그래프 탐색 및 벡터 검색 |
| 100만 노드 | 동시성 및 저장소 확장 |

3. **관리 지표의 오류 은폐 가능성**
   - `onedoor.rs`의 `graph_stats()`는 테이블 조회 실패 시 `unwrap_or(0)`으로 0을 반환하는 경로가 확인되었습니다.
   - 빈 테이블과 SQL 오류를 구분하지 못하면 실제 데이터가 있어도 0개로 표시될 수 있습니다.
   - 개선: 정상적인 빈 데이터와 조회 실패를 분리하고, 실패 시 `unavailable` 또는 오류 상태를 반환합니다.

---

## 4. RAG 및 Retrieval Engine

검토한 파일:

- `rust/lattice-retrieval/src/service_hybrid.rs`
- `rust/lattice-retrieval/src/hybrid.rs`
- `rust/lattice-retrieval/src/bm25/graph_rag.rs`
- `rust/lattice-retrieval/src/query_embed.rs`
- `rust/lattice-retrieval/src/vector_alignment.rs`
- `rust/lattice-retrieval/src/rag_eval.rs`

현재 검색은 BM25F, Vector Search, Knowledge Graph 확장을 결합하고 RRF/Hybrid Fusion으로 통합하는 구조입니다.

### 4.1 잘한 점

1. **BM25F**
   - 제목·요약 등 필드별 가중치를 고려할 수 있습니다.
   - 한국어 조사 처리, CJK 바이그램, 식별자 서브워드 처리는 한국어·영어 혼합 지식 검색에 적합한 접근입니다.

2. **GraphRAG**
   - `graph_rag.rs`에서 1-hop 및 2-hop 관계 확장을 수행합니다.
   - 직접 관련 없는 문서도 중간 개념을 통해 검색 후보로 가져올 수 있습니다.

3. **Vector Alignment**
   - 모델 ID와 차원 등 벡터 계약을 확인하고 `reindex_required` 같은 상태를 구분하는 것은 운영상 중요합니다.

4. **검색 성능 구조**
   - HNSW 후보 검색과 정확한 재정렬을 결합하는 접근은 속도와 정확도를 함께 고려합니다.

### 4.2 가장 중요한 개선: 실제 RAG 품질 검증

`rust/lattice-retrieval/src/rag_eval.rs`를 확인했습니다. 평가 프레임워크에는 다음 지표가 있습니다.

- Recall@5 / Recall@10
- NDCG@10
- Citation Precision / Recall
- Faithfulness
- Answer Relevance
- Superseded Exclusion Rate
- 한국어 Recall
- Cross-model Jaccard
- 검색 및 인덱싱 지연시간

다만 다음 한계를 구분해야 합니다.

- 평가용 시맨틱 벡터 일부는 실제 임베딩 모델이 아니라 `synthetic_semantic_vector()`로 생성한 결정론적 테스트 벡터입니다.
- `faithfulness_and_relevance()`는 생성 답변의 사실성을 직접 판정하는 LLM 평가가 아니라, 검색 증거와 요구된 주장 사이의 토큰 일치도를 계산합니다.

따라서 현재 평가 프레임워크만으로 실제 Gemma, Qwen 등 모델 연결 시 답변 품질을 입증할 수는 없습니다.

### 4.3 개선 방향

실제 사용자 질문과 정답 근거를 포함한 RAG Evaluation Dataset을 만듭니다.

| 평가 영역 | 추가 평가 |
|---|---|
| 검색 | 실제 질문의 정답 문서 검색률 |
| 의미 검색 | 동의어, 문맥, 간접 질문 |
| 한국어 | 조사, 축약어, 복합명사 |
| 다중 문서 | 여러 파일을 연결한 답변 |
| 시간 | 과거 정보와 최신 정보 구분 |
| 충돌 | 서로 다른 내용의 기억 구분 |
| 생성 | 답변 사실성과 근거 일치 |
| 모델 교체 | 모델별 검색 및 답변 품질 비교 |

평가는 반드시 두 영역으로 분리합니다.

- **Retrieval Evaluation:** 필요한 기억을 제대로 찾았는가?
- **Generation Evaluation:** 찾은 기억으로 정확한 답변을 만들었는가?

---

## 5. Knowledge Graph 및 Brain Intelligence

검토 파일:

- `rust/lattice-core/src/graph_write/`
- `rust/lattice-retrieval/src/neighborhood_synthesis.rs`
- `rust/lattice-retrieval/src/bm25/graph_rag.rs`
- `frontend/src/pages/brain/GraphNodeInspector.tsx`
- `frontend/src/features/brain/MultiHopSynthesisView.tsx`
- `frontend/src/features/brain/ContradictionInspector.tsx`

### 5.1 잘한 점

1. **2-Hop Reasoning**
   - 저장된 그래프의 관계를 탐색하고 중간 개념을 통해 관계를 설명하는 기반이 있습니다.

2. **Contradiction Detection**
   - `CONTRADICTS`, `SUPERSEDES` 관계를 활용해 상충되는 지식을 식별하고 검토하는 구조가 있습니다.

3. **사용자 검토**
   - 기존 정보 유지, 새 정보로 교체, 시간에 따른 공존 등 사용자가 판단할 수 있는 구조는 개인 지식 관리에 적절합니다.

### 5.2 아쉬운 점

현재의 관계 탐색만으로는 동일 개체의 시간에 따른 변화와 맥락을 완전히 이해하기 어렵습니다.

예시:

- 1월: 프로젝트 A는 Python으로 개발한다.
- 3월: 프로젝트 A를 Rust로 이전하기로 결정했다.
- 5월: Python 모듈 일부는 유지하기로 했다.

Brain은 현재 상태가 Rust 중심이지만 일부 Python 모듈이 남아 있다는 결론을 내리고, 각 판단의 출처와 시간적 근거를 제시할 수 있어야 합니다.

### 5.3 개선 제안

| 기능 | 개선 방향 |
|---|---|
| Entity Resolution | 동일 인물·프로젝트·개념 통합 |
| Temporal Knowledge | 시간에 따른 지식 변화 |
| Confidence | 추출된 관계의 신뢰도 |
| Contradiction | 의미 기반 충돌 탐지 |
| Causal Graph | 원인과 결과 관계 |
| Knowledge Evolution | 생성·변경·폐기 이력 |
| Contextual Synthesis | 여러 지식의 맥락적 종합 |

특히 Entity Resolution을 우선 강화합니다. 같은 프로젝트가 문서마다 다른 이름으로 등장하더라도 동일한 개체로 연결되어야 합니다.

---

## 6. 데이터 수집 및 인덱싱 파이프라인

검토 파일:

- `rust/lattice-ingest/src/`
- `rust/lattice-core/src/graph_write/ingest.rs`
- `rust/lattice-core/src/graph_write/vectors.rs`
- `latticeai/api/worker_compute.py`
- `latticeai/runtime/build_phases/worker_profile.py`

### 6.1 잘한 점

현재 흐름은 대략 다음과 같습니다.

1. 파일 및 폴더 탐색
2. 파일 변경 여부 확인
3. Fingerprint / SHA256 확인
4. Python Worker 파싱 및 추출
5. 청킹 및 메타데이터 생성
6. Embedding 생성
7. Rust GraphWriter 저장
8. 검색 인덱스 갱신

변경되지 않은 파일의 재처리 방지, 파일 변경 시 필요한 데이터만 처리, 임베딩 작업의 트랜잭션 외부 실행, 실패 작업 큐, 삭제 파일에 대한 그래프 정리 등이 긍정적입니다.

### 6.2 아쉬운 점 및 개선

1. **실시간 파일 동기화**
   - 현재 폴더 Watch는 Polling 방식입니다.
   - 개선: OS Native File Watcher + Debounce + Batch 처리 + 이벤트 유실 시 Reconciliation Scan + 중복 이벤트 제거.
   - Polling을 완전히 없애기보다 Native Watcher와 주기적인 전체 점검을 병행하는 것이 안전합니다.

2. **멀티모달 수집**
   - 코드에 문서 파싱, 임베딩, ASR 등의 처리 경로가 있는 것과 실제 파일 수집 파이프라인에서 자동 활용되는 것은 다릅니다.
   - 아키텍처상 이미지·비디오 멀티모달 기능의 HTTP 연결이 아직 제공되지 않는다고 명시된 부분이 있어 실제 연결 상태를 별도로 확인해야 합니다.
   - 개선: 이미지 OCR/Vision, PDF 페이지 이미지 분석, 표·차트 추출, 영상 프레임 분석, 음성·영상 타임스탬프 연결, 이미지와 문서 간 관계 추출.

3. **수집 품질 관리**
   - 중복 문서 감지
   - 중요도 분류
   - 정보 신선도 평가
   - 문서별 추출 품질 점수
   - 잘못된 파싱 결과 감지
   - 재수집 우선순위 결정

---

## 7. Agent Engine 및 Tool Execution

검토 파일:

- `rust/lattice-agent/src/kernel/permission.rs`
- `rust/lattice-agent/src/kernel/breaker.rs`
- `rust/lattice-agent/src/tools/sandbox.rs`
- `rust/lattice-agent/src/`
- `rust/lattice-platform/src/toolsurface/`

### 7.1 잘한 점

- Permission Mode: `strict`, `trusted`, `bypass`
- Circuit Breaker
- 도구 실행 전 권한 검사
- 실행 경로 제한
- 변경 제안 및 승인
- 실행 기록
- Tool Registry
- MCP 통합
- Hook 기반 실행 통제

Permission Mode와 Network Boundary를 분리하는 것이 중요합니다. 도구 실행을 자동 승인했다고 Cloud 데이터 전송까지 자동 허용되는 것은 아닙니다.

### 7.2 아쉬운 점 및 개선

1. **장기 작업 관리**
   - 100단계 작업의 상태 저장
   - 실패 작업 재개
   - 중간 결과물 저장
   - 작업 간 의존성
   - 하위 Agent 위임
   - 최종 결과 검증
   - 개선: 단순 Agent Loop를 넘어 Durable Workflow 구조를 도입합니다.

2. **실행 환경 보안**
   - `sandbox.rs`에서 심볼릭 링크를 고려한 경로 해석을 수행합니다.
   - 경로 검사 시점과 파일 오픈 시점 사이에 링크가 바뀌는 TOCTOU 경쟁 조건은 별도 검증이 필요합니다.
   - 개선: Unix `openat` / `O_NOFOLLOW` 계열 보호, Windows Reparse Point 검증, 프로세스별 권한 격리, 동시 파일 수정 충돌 감지.

3. **Multi-Agent**
   - Brain Orchestrator: 전체 맥락, 계획, 결과 검증
   - Research Agent: 지식 탐색 및 조사
   - Knowledge Agent: 기억 생성 및 정리
   - Execution Agent: 도구 실행 및 자동화
   - Verification Agent: 근거 및 결과 검증
   - Agent마다 별도 기억을 만드는 대신 동일한 Brain을 참조하도록 합니다.

---

## 8. Python Worker 및 모델 런타임

검토 파일:

- `latticeai/worker_app.py`
- `latticeai/api/worker_compute.py`
- `latticeai/runtime/build_phases/worker_profile.py`
- `latticeai/core/embedding_providers/text.py`
- `latticeai/services/model_runtime/download.py`

### 8.1 잘한 점

- Python Worker가 제품 전체를 관리하지 않고 계산에 집중합니다.
- 모델 카탈로그, 임베딩 제공자, 다운로드 및 취소, 문서 렌더링을 분리했습니다.
- EmbeddingGemma 2 공개 미러 및 양자화 모델 fallback을 고려한 부분이 있습니다.

### 8.2 아쉬운 점 및 개선

1. **모델 호환성 검증**
   - 정적 메타데이터 검증만으로 모델의 실제 로딩과 추론 가능성을 보장할 수 없습니다.
   - 개선: 실제 다운로드, 토크나이저 호환성, 모델 로딩, 이미지 입력, 추론, 메모리, 장시간 실행 테스트.

2. **모델 역할 추상화**
   - Generation Model
   - Embedding Model
   - Reranking Model
   - Vision Model
   - ASR Model
   - Extraction Model
   - 모델 교체 시 재색인 필요 여부와 기존 지식 호환성을 명확히 표시합니다.

3. **모델 실행 상태**
   - 다운로드 → 검증 → 설치 → 로딩 → 추론 가능 → 정상 운영 상태를 구분합니다.

---

## 9. Frontend 및 사용자 경험

검토 파일:

- `frontend/src/pages/Brain.tsx`
- `frontend/src/features/brain/BrainConversation.tsx`
- `frontend/src/features/brain/MultiHopSynthesisView.tsx`
- `frontend/src/pages/brain/GraphNodeInspector.tsx`
- `frontend/src/features/brain/ContradictionInspector.tsx`
- `frontend/src/features/brain/AnswerProof.tsx`
- `frontend/src/api/client.ts`

### 9.1 잘한 점

- Route 단위 Lazy Loading
- Feature 단위 코드 분리
- React Query 활용
- API 실패 상태 처리
- 한글/영어 i18n
- Error Boundary
- 답변 근거 표시
- 지식 그래프 인스펙터
- 변경 제안 인라인 승인

### 9.2 아쉬운 점 및 개선

1. **기능 중심 화면의 복잡도**
   - 사용자는 BM25F, Vector Index, Provenance를 이해할 필요가 없습니다.
   - 사용자가 알고 싶은 것은 축적된 지식, AI가 기억하는 내용, 기억의 정확성, 변경 내역, 답변 근거입니다.
   - 내부 엔진은 필요할 때 상세 화면에서 확인하도록 합니다.

2. **Brain Chat과 Knowledge Graph 통합**
   - 질문 → 관련 기억 탐색 → 답변 생성 → 출처 확인 → 기억 수정 → 그래프 반영 흐름이 자연스럽게 연결되어야 합니다.

3. **기억 생성 과정의 가시성**
   - 인덱싱 완료만 표시하지 말고 어떤 지식이 추출되고 어떤 관계가 형성되었는지 보여줍니다.
   - 기본 화면은 단순하게 유지하고 상세 정보는 펼쳐서 확인하도록 합니다.

---

## 10. Tauri 및 Desktop Runtime

검토 파일:

- `src-tauri/src/main.rs`
- `src-tauri/src/backend.rs`
- `src-tauri/src/topology.rs`
- `rust/lattice-host/src/supervisor/`

### 10.1 잘한 점

- Rust Host 중심 실행 구조
- Worker 프로세스 실행
- Health Check
- 재시작
- Graceful Shutdown
- Backend 상태 확인
- Loopback 통신

### 10.2 아쉬운 점 및 개선

운영체제별 실제 설치 및 실행 검증이 필요합니다.

| 환경 | 검증 사항 |
|---|---|
| macOS | Apple Silicon, MLX, 권한, Gatekeeper |
| Windows | 프로세스 종료, 경로, 모델 런타임 |
| Linux | 패키지 및 GPU 런타임 |
| 공통 | Worker 장애, 강제 종료, 복구 |

Windows와 macOS의 실제 데스크톱 설치 및 실행 Smoke Test를 별도로 구축하는 것을 권장합니다.

---

## 11. 보안 및 개인정보 보호

검토 파일:

- `rust/lattice-auth/`
- `rust/lattice-agent/src/kernel/`
- `rust/lattice-platform/src/knowledge/network_boundary_guard.rs`
- `rust/lattice-chat/src/boundary.rs`
- `rust/lattice-chat/src/cloud/context.rs`
- `SECURITY.md`
- `.github/workflows/dependency-audit.yml`

### 11.1 잘한 점

1. **Local-First 정책**
   - 사용자 지식과 대화가 외부로 전송되지 않도록 하는 기본 방향이 제품 철학과 부합합니다.

2. **Permission Mode와 Network Boundary 분리**
   - AI가 도구를 실행해도 되는지와 지식이 외부로 나가도 되는지를 별도로 판단합니다.

3. **Cloud Egress 제어**
   - 전체 Knowledge Graph 대신 필요한 정보만 추출하고, 민감 정보 필터링과 Cloud 전송 동의, 토큰 예산을 고려합니다.

### 11.2 아쉬운 점 및 개선

1. **Cloud Preview와 실제 Payload 일치**
   - Preview 결과와 실제 전송 결과가 다르면 안 됩니다.
   - 공통 Context Builder를 사용하고 실제 전송 직전 Payload와 Preview 결과를 비교하는 테스트를 추가합니다.

2. **Rust 의존성 감사**
   - 확인한 `dependency-audit.yml`에는 Python과 npm 취약점 감사 및 SBOM 생성이 있습니다.
   - 해당 Workflow에는 Rust용 `cargo audit`와 Rust SBOM 생성이 포함되어 있지 않습니다.
   - 개선: `cargo audit`, `cargo deny`, Rust CycloneDX SBOM, 라이선스 정책 검사, 정기 의존성 업데이트.

3. **데이터 암호화**
   - `.latticebrain` 아카이브 암호화와 로컬 SQLite 데이터 보호는 별개의 문제입니다.
   - 개선: 데이터베이스 암호화, 키 관리 및 회전, 백업 암호화, 임시 파일 보호, 로그 민감정보 제거, OS Keychain 연동.

---

## 12. 테스트 및 CI/CD

확인한 파일:

- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`
- `.github/workflows/publish.yml`
- `.github/workflows/dependency-audit.yml`
- `scripts/run_rust_tests.mjs`
- `rust/lattice-retrieval/tests/rag_eval.rs`
- `tests/unit/test_worker_app.py`

### 12.1 잘한 점

현재 테스트 체계에는 다음 종류가 있습니다.

| 테스트 종류 | 검증 대상 |
|---|---|
| Rust Unit | 개별 크레이트 |
| Rust Parity | 기존 구현과 결과 일치 |
| Python Unit | Worker 및 서비스 |
| Frontend Vitest | 컴포넌트 및 UI 로직 |
| Integration | 실제 Host와 Worker 연동 |
| Visual | 화면 렌더링 |
| RAG Evaluation | 검색 품질 |
| Dependency Audit | 외부 라이브러리 취약점 |
| Release Smoke | 배포 산출물 |

Golden Fixture를 이용해 기존 동작과 새로운 Rust 구현 결과를 비교하는 방식도 긍정적입니다.

### 12.2 아쉬운 점 및 개선

1. **Rust 테스트 실행 방식 불일치**
   - `scripts/run_rust_tests.mjs`는 `cargo-nextest`가 있으면 병렬 실행을 사용합니다.
   - 확인한 `.github/workflows/ci.yml`의 Rust Job은 `cargo test --workspace`를 직접 실행합니다.
   - 로컬과 CI의 테스트 실행 전략을 통일하는 것이 좋습니다.

   권장 검토 명령:

   ```bash
   cargo nextest run --workspace --no-fail-fast
   cargo test --workspace --doc
   ```

   단, CI Runner에서 Nextest의 프로세스 격리와 SQLite 테스트 충돌 여부를 먼저 검증합니다.

2. **Golden Test의 과도한 고정 방지**
   - Compatibility Golden: 이전 버전과 호환해야 하는 계약
   - Behavior Test: 현재 기능의 정확성
   - Quality Evaluation: 실제 검색 및 답변 품질
   - Performance Benchmark: 실행 효율
   - 기존 동작을 보존할지 개선할지 목적을 분리합니다.

3. **Coverage와 실제 품질 구분**
   - 코드 커버리지가 높아도 실제 질문에 정확히 답한다는 뜻은 아닙니다.
   - 코드 커버리지 외에 RAG 품질, 답변 근거, 장애 복구, 성능 지표를 관리합니다.

4. **Rust CI 최적화**
   - Nextest 도입
   - `cargo-chef` 기반 의존성 레이어 캐싱 검토
   - 변경된 크레이트의 영향 범위 분석
   - 불필요한 전체 테스트 실행 최소화
   - PR 테스트와 정기 전체 테스트 분리
   - 느린 테스트 자동 측정 및 추적
   - 단, Merge 전 전체 Workspace 검증은 유지합니다.

---

## 13. 가장 중요한 구조적 문제 5가지

### 1. 실제 RAG 품질 검증 부족 — 최우선

검색 알고리즘과 평가 프레임워크는 발전했지만, 실제 사용자 질문과 실제 모델을 통한 품질 검증이 더 필요합니다.

### 2. 지식의 의미적 이해 — 최우선

동일 개체 통합, 시간에 따른 변화, 복합적인 맥락 추론을 강화해야 합니다.

### 3. 자동 데이터 수집 및 멀티모달 — 높음

텍스트 중심 인덱싱을 넘어 이미지, 영상, 음성, 표 등 다양한 정보가 Brain에 축적되어야 합니다.

### 4. Agent의 지속적인 작업 능력 — 높음

한 번의 질문에 답하는 Agent가 아니라 장기 작업을 계획·실행·복구·검증하는 시스템으로 발전해야 합니다.

### 5. 테스트 비용과 운영 효율 — 높음

기능을 추가할 때마다 전체 검증 시간이 과도하게 증가하지 않도록 테스트 구조를 최적화해야 합니다.

---

## 14. 향후 개발 로드맵

### Phase 1. Brain Engine 완성

**가장 먼저 진행 권장**

| 개발 항목 | 목표 |
|---|---|
| RAG Evaluation | 실제 질문 기반 평가 |
| Entity Resolution | 동일 개체 통합 |
| Temporal Memory | 시간에 따른 기억 관리 |
| Confidence Scoring | 지식 신뢰도 |
| Contradiction Engine | 의미 기반 충돌 탐지 |
| Context Synthesis | 여러 기억의 통합 |

### Phase 2. Universal Ingestion

| 개발 항목 | 목표 |
|---|---|
| Native File Watch | 실시간 파일 변경 |
| Image Understanding | 이미지 내용 이해 |
| Video Understanding | 영상 프레임 및 장면 분석 |
| Audio Understanding | 음성 및 대화 분석 |
| Document Structure | 표, 차트, 문서 구조 |
| Auto Classification | 자동 지식 분류 |

핵심은 사용자가 직접 지식을 정리하지 않아도 Brain이 자동으로 축적하는 것입니다.

### Phase 3. Agentic Brain

| 개발 항목 | 목표 |
|---|---|
| Durable Task State | 작업 상태 영속화 |
| Multi-Agent | 전문 Agent 협업 |
| Long-running Workflow | 장기 작업 |
| Self Verification | 결과 자체 검증 |
| Knowledge Maintenance | 기억의 자동 관리 |
| Proactive Intelligence | 필요한 정보 선제 제안 |

### Phase 4. Model-Agnostic Intelligence

어떤 모델을 사용하더라도 동일한 Brain을 활용할 수 있어야 합니다.

```text
Gemma (Local) ─┐
Qwen (Local) ──┼── Universal Brain Interface
Cloud LLM ─────┘       │
                       ├── Context
                       ├── Retrieval
                       ├── Evidence
                       ├── Memory
                       └── Tools
                              │
                       Lattice Knowledge Core
                       ├── SQLite
                       ├── Knowledge Graph
                       ├── Provenance
                       └── Embeddings
```

LLM은 Brain의 소유자가 아니라 Brain을 사용하는 추론 엔진이어야 합니다. 모델을 교체해도 지식, 경험, 의사결정, 관계, 작업 이력, 개인 맥락이 유지되어야 합니다.

---

## 15. 최종 개선 우선순위

| 순위 | 작업 | 우선순위 | 기대 효과 |
|---:|---|---|---|
| 1 | 실제 RAG 품질 평가 | P0 | Brain 정확도 확보 |
| 2 | Entity Resolution | P0 | 지식 통합 |
| 3 | Temporal Knowledge | P0 | 기억의 시간적 이해 |
| 4 | 검색 결과 및 근거 검증 | P0 | 환각 감소 |
| 5 | 데이터 수집 자동화 | P1 | 지식 축적 자동화 |
| 6 | 멀티모달 파이프라인 | P1 | 정보 이해 범위 확대 |
| 7 | Durable Agent Workflow | P1 | 장기 작업 수행 |
| 8 | Multi-Agent 협업 | P2 | 복합 작업 능력 |
| 9 | UI 및 UX 통합 | P2 | 제품 사용성 |
| 10 | Rust 테스트 최적화 | P1 | 개발 생산성 |

P0를 먼저 완료하고 P1을 병행하는 방식을 권장합니다. P2는 Brain Engine의 품질과 안정성을 확보한 뒤 확장합니다.

---

## 16. 반드시 추가할 제품 관점 테스트

| 테스트 시나리오 | 기대 결과 |
|---|---|
| 1,000개 문서 수집 | 중복 없이 정상 색인 |
| 동일 파일 100회 재수집 | 불필요한 재색인 방지 |
| 문서 수정 | 변경된 내용만 갱신 |
| 파일 삭제 | 관련 지식 및 관계 정리 |
| 모델 교체 | 기존 지식 유지 |
| 임베딩 모델 교체 | 필요한 벡터 재색인 |
| 한국어 질문 | 관련 지식 검색 |
| 영어 질문 | 관련 지식 검색 |
| 한영 혼합 질문 | 언어 간 검색 |
| 과거와 현재 정보 충돌 | 시간에 따른 상태 구분 |
| 서로 다른 문서의 연관 질문 | 복수 문서 통합 |
| 근거가 없는 질문 | 모른다는 답변 |
| 모델 실행 중단 | 작업 상태 복구 |
| Cloud 비활성화 | 데이터 외부 전송 차단 |

### 16.1 Model Replacement Test

1. Gemma로 Brain에 질문합니다.
2. 동일한 질문을 Qwen으로 수행합니다.
3. Cloud LLM으로도 수행합니다.
4. 동일한 Brain 데이터와 검색 조건을 사용합니다.
5. 검색 결과, 근거, 답변 품질을 비교합니다.

이 테스트는 모델을 바꾸어도 지식이 유지된다는 제품 철학을 검증합니다.

---

## 17. 최종 결론

Lattice AI는 검색엔진과 데이터 저장소의 기반을 넘어 Digital Brain 플랫폼으로 발전할 수 있는 구조를 갖추고 있습니다.

현재 가장 부족한 것은 RAG 알고리즘이나 데이터베이스 기술 자체라기보다, 축적된 지식으로부터 새로운 맥락과 의미를 만들어내는 능력입니다.

### 현재 확보한 기반

- Rust 중심의 데이터 관리
- Hybrid RAG
- Knowledge Graph
- Provenance
- Agent 실행 엔진
- 모델 교체 가능한 구조
- Local-First 보안 경계

### 앞으로 집중할 부분

- 지식의 의미적 통합
- 시간과 경험을 이해하는 기억
- 실제 AI 답변 품질
- 자동 지식 축적
- 지속적인 추론과 검증

궁극적으로 Brain은 다음 질문에 답할 수 있어야 합니다.

- 내가 과거에 어떤 결정을 내렸는가?
- 왜 그런 결정을 내렸는가?
- 이후에 무엇이 바뀌었는가?
- 현재 정보 중 서로 모순되는 것은 무엇인가?
- 여러 문서와 경험을 종합하면 어떤 새로운 결론을 도출할 수 있는가?
- 다른 모델을 사용해도 동일한 지식을 활용할 수 있는가?

**지금은 기능을 더 많이 만드는 것보다 Brain Engine의 지능을 높이는 것이 중요합니다.**

---

## 18. 참고 소스

- [GitHub 저장소](https://github.com/TaeSooPark-PTS/LatticeAI)
- [Rust Core](https://github.com/TaeSooPark-PTS/LatticeAI/tree/main/rust/lattice-core)
- [Retrieval Engine](https://github.com/TaeSooPark-PTS/LatticeAI/tree/main/rust/lattice-retrieval)
- [Agent Engine](https://github.com/TaeSooPark-PTS/LatticeAI/tree/main/rust/lattice-agent)
- [Frontend](https://github.com/TaeSooPark-PTS/LatticeAI/tree/main/frontend)
- [릴리스 노트](https://github.com/TaeSooPark-PTS/LatticeAI/blob/main/RELEASE_NOTES.md)

## 19. 리뷰 범위와 한계

이번 리뷰는 주요 소스 파일을 열어 Rust, Python, TypeScript, Tauri, 테스트, CI Workflow를 검토한 정적 리뷰입니다.

다음 작업은 수행하지 않았습니다.

- 저장소의 모든 파일을 한 줄씩 전수 검사
- 실제 Mac에서 전체 빌드 및 테스트 실행
- 메모리 및 검색 성능 실측
- 실제 Gemma/Qwen 모델을 연결한 장시간 RAG 평가
- 모든 운영체제에서 설치 및 실행 검증

따라서 개선 항목은 코드와 구조에서 발견한 위험 및 발전 방향이며, 실제 결함 여부와 우선순위는 재현 테스트와 실행 검증을 통해 확정해야 합니다.
