[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../CODE_REVIEW_2026-11-20.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./CODE_REVIEW_2026-11-20.md)

# Lattice AI 전체 코드 리뷰 및 개선 로드맵

> 검토 기준일: 2026-11-20  
> 저장소: https://github.com/TaeSooPark-PTS/LatticeAI  
> 검토 브랜치: `main`  
> 검토 기준 커밋: `360892d320f9ac018229ca453e4cd86cad68cbb3`  
> 저장소 파일 수: 2,041개 (Git tree 기준, 디렉터리 및 파일 구성에 따라 집계 기준이 달라질 수 있음)

---

## 1. 문서 목적 및 검토 범위

이 문서는 Lattice AI 저장소의 README나 Markdown 문서만 요약한 것이 아니라, Git tree를 기준으로 주요 구현 파일과 프로젝트 구성 요소를 확인하고 정리한 정적 코드 리뷰다.

검토한 주요 영역:

- Rust Workspace 및 Brain Core
- Knowledge Graph / GraphWriter / Schema
- Retrieval / BM25F / Vector / GraphRAG
- Embedding Provider 및 Index Contract
- Agent Kernel / Agent Loop / Adaptive Harness
- Rust Host / Gateway / Python Worker
- React / TypeScript Frontend
- Tauri Desktop / CLI / VS Code 및 Browser Extension
- Security / Workspace Isolation / Approval
- GitHub Actions CI/CD / 테스트 및 배포 구조

### 검토 한계

이 검토는 저장소 코드와 문서에 대한 정적 검토다. 이 세션에서 전체 테스트를 직접 실행하거나, 실제 Mac/Windows 환경에서 앱을 설치·실행하거나, 대규모 데이터셋으로 성능을 측정하지 않았다. 따라서 아래 내용은 다음 세 가지로 구분해서 해석해야 한다.

1. **코드에서 확인한 구조:** 실제 파일과 구현을 확인한 사항
2. **검증 필요 항목:** 코드 구조상 위험 또는 추가 테스트가 필요해 보이는 사항
3. **개선 제안:** 제품 목표와 장기 운영성을 고려한 권고

벤치마크 수치나 성능 개선 효과는 실제 재현 가능한 실험 없이는 확정하지 않는다. 이 문서는 정식 보안 감사나 실행 기반 품질 인증을 대체하지 않는다.

---

## 2. 종합 평가

Lattice AI는 단일 챗봇이나 단순 RAG 애플리케이션보다 넓은 범위의 플랫폼 구조를 갖추고 있다. Rust 기반 Host/Core와 Python AI Worker, React/Tauri 클라이언트, Knowledge Graph 및 Hybrid Retrieval, Agent 실행 계층이 함께 존재한다.

가장 중요한 전략적 판단은 **새 기능을 계속 늘리는 것보다 Brain의 정확성·일관성·복구 가능성·측정 가능성을 먼저 완성하는 것**이다.

### 강점

- Rust 중심의 제품 핵심 경로와 Python AI 연산 계층을 분리하려는 구조
- GraphWriter를 통한 지식 쓰기 경로의 중앙화
- BM25F, Vector, GraphRAG를 결합하는 Hybrid Retrieval 방향
- Embedding 모델의 계약과 인덱스 호환성을 고려한 설계
- Agent 실행 능력을 모델 이름만으로 추정하지 않고 Probe로 평가하려는 Adaptive Harness
- Core/Runtime Rust 테스트 Job 분리, Cargo Cache 및 nextest 활용
- Local-first와 사용자 승인 중심의 제품 방향

### 가장 큰 리스크

1. 검색 및 답변 품질을 객관적으로 측정할 평가 데이터셋과 회귀 체계가 충분한가?
2. Knowledge Graph가 중복·충돌·시간에 따른 변경을 정확하게 관리하는가?
3. Embedding 모델 변경 도중 기존 인덱스와 데이터가 안전하게 유지되는가?
4. Agent가 도구 실행에 성공하는 것과 최종 결과가 정확한 것을 구분해 평가하는가?
5. Rust Host와 Python Worker 사이의 데이터 계약 및 장애 복구가 충분히 검증되는가?
6. Workspace 권한이 API뿐 아니라 검색·Graph Expansion·파일·Agent Tool 전 경로에 일관되게 적용되는가?
7. 실제 Windows 및 macOS 사용자 환경에서 패키지 설치부터 종료·복구까지 검증되는가?

---

## 3. 아키텍처 리뷰

현재 확인한 구조를 개념적으로 나타내면 다음과 같다.

```text
React / TypeScript / Tauri / CLI / VS Code / Browser Extension
                         |
                         v
                  Rust Host / Gateway
                         |
            +------------+------------+
            |                         |
            v                         v
      Rust Brain Core           Python AI Worker
      - Graph / Storage          - LLM Provider
      - GraphWriter              - Embedding Provider
      - Retrieval                - Parsing / AI Tasks
      - Jobs / Auth
            |
            v
      Local Knowledge Store
      - SQLite
      - Graph / Metadata
      - Embedding Index
      - Provenance / Archives
```

### 평가

Rust가 데이터와 제품의 핵심 경로를 담당하고 Python이 모델 및 AI 연산을 담당하는 방향은 합리적이다. 다만 언어를 나눈 것만으로 경계가 명확해지는 것은 아니다. API 계약, 데이터 소유권, 버전 호환성, 오류 처리 및 작업 재시도 정책이 함께 정의되어야 한다.

### 개선 권고

- Rust/Python 간 Request·Response Schema를 단일 기준으로 관리한다.
- API Contract Test와 Rust/Python Parity Test를 CI에서 수행한다.
- 데이터베이스 쓰기 주체와 읽기 모델을 명확히 문서화한다.
- Worker 장애·재시작·버전 불일치·타임아웃을 통합 테스트한다.
- 장기적으로 Python에 남아 있는 레거시 데이터 처리 로직과 실제 AI 연산 로직을 분리한다.
- 프로세스 경계에서 작업 ID, 상관관계 ID, 오류 코드, 취소 및 재시도 규칙을 표준화한다.

---

## 4. 섹터별 상세 리뷰

## 4.1 Knowledge Graph / Brain Core

주요 경로:
- `rust/lattice-core/`
- `rust/lattice-core/src/graph_write/`
- `rust/lattice-core/src/graph_write/schema.rs`
- `lattice_brain/`

### 잘한 점

- Rust 기반 GraphWriter를 중심으로 쓰기 경로를 관리하려는 방향
- SQLite 트랜잭션을 통한 데이터 변경
- 노드와 엣지 기반 지식 관계 모델
- 출처 및 지식 생명주기를 고려하는 설계
- 기존 Python 스키마와의 호환성을 고려한 구현

### 아쉬운 점 및 위험

**1) 스키마 이중 관리 위험**

Rust와 Python에 스키마 정의 또는 관련 데이터 계약이 중복되면 시간이 지날수록 필드·제약조건·기본값·마이그레이션 동작이 달라질 수 있다.

**개선**
- Schema Version 및 Migration Registry를 명확히 관리
- 마이그레이션 전후 Schema Parity Test 추가
- 오래된 DB에서 최신 버전까지의 Upgrade Test 수행
- 마이그레이션 실패 및 중단 시 Rollback/Recovery 테스트
- 새 버전에서 이전 버전으로의 호환성 정책 명시

**2) 시간에 따른 지식 변경 관리**

같은 사실이 서로 다른 시점에 수집될 수 있다. 최신 사실과 과거 사실을 단순히 별도 노드로 저장하는 것만으로는 충분하지 않다.

**개선**
- `valid_from`, `valid_to`, `observed_at` 등 시간 의미 정의
- Active / Superseded / Expired 상태 정책 표준화
- Source Confidence와 Evidence Provenance 관리
- 충돌 탐지 및 사용자 확인 흐름
- 중복 지식 병합 시 원본 출처 보존
- 지식 변경 이력 및 복구 기능

**3) 관계 품질**

잘못된 Entity Resolution 또는 관계 추출은 GraphRAG 결과를 오염시킬 수 있다.

**개선**
- Entity Deduplication 평가셋
- Relation Extraction Precision/Recall 측정
- 잘못된 관계를 되돌리는 정정·삭제 정책
- 관계 생성 근거와 출처 저장
- 자동 병합과 사용자 승인 병합을 구분

**권장 우선순위: P0/P1**

---

## 4.2 RAG / Retrieval Engine

주요 경로:
- `rust/lattice-retrieval/`
- `rust/lattice-retrieval/src/bm25/`
- `rust/lattice-retrieval/src/bm25/sidecar.rs`
- `rust/lattice-retrieval/src/bm25/graph_rag.rs`
- `rust/lattice-retrieval/src/bm25/scoring.rs`

### 잘한 점

- BM25F 기반 필드별 검색
- Vector Semantic Search와의 결합
- GraphRAG를 통한 관계 기반 확장
- RRF 방식의 검색 결과 통합
- Sidecar Index 및 Incremental Indexing 방향
- Historical Knowledge를 검색 후보에서 구분하려는 접근
- Vector Exact Rescore를 통한 정밀 재평가 방향

### 아쉬운 점 및 위험

검색 구현이 복잡해질수록 코드의 정교함과 실제 검색 품질은 별개가 된다. 최종 사용자의 질문에 필요한 근거가 상위 결과에 포함되는지 측정하는 체계가 중요하다.

**개선**
- 고정된 Query/Expected Evidence 평가셋 구축
- Recall@5/10, MRR, nDCG 측정
- BM25F 단독, Vector 단독, GraphRAG 단독, Hybrid 결과 비교
- RRF 파라미터 변경에 대한 회귀 테스트
- Graph Expansion 전후 Precision 비교
- Active/Superseded 지식 구분 테스트
- 한국어 띄어쓰기·조사·복합어·영문 혼합 질의 평가
- Workspace 경계 및 접근 권한 검색 테스트
- 검색 지연시간 p50/p95/p99 및 인덱스 크기 측정

### 권장 평가 데이터셋

최소한 다음 유형을 포함한다.

| 평가 유형 | 예시 |
|---|---|
| Exact Match | 문서명, 제품명, ID 검색 |
| Semantic | 표현이 다른 동일 의미 질문 |
| Multi-hop | 두 개 이상 지식 관계를 연결하는 질문 |
| Temporal | 과거 값과 최신 값 구분 |
| Conflict | 서로 다른 출처의 상충 정보 |
| Korean | 조사·띄어쓰기·복합어 |
| Scope | 다른 Workspace 정보 차단 |
| No-answer | 근거가 없을 때 모른다고 답변 |

**권장 우선순위: P0**

---

## 4.3 Embedding / Vector Index

주요 경로:
- `latticeai/core/embedding_providers/`
- `latticeai/core/embedding_providers/text.py`
- `rust/lattice-core/src/embeddings/`
- `rust/lattice-retrieval/`

### 잘한 점

- 여러 Embedding Provider를 수용하는 방향
- 모델 및 차원 등 인덱스 호환성을 명시적으로 관리하려는 계약
- Local Model 및 다양한 런타임을 고려하는 확장성

### 아쉬운 점 및 위험

**1) 모델 변경 중 인덱스 혼합**

Embedding 모델이 바뀌면 기존 벡터와 신규 벡터가 동일한 공간이라고 가정해서는 안 된다.

**개선**
1. 신규 모델용 별도 인덱스 생성
2. 기존 인덱스와 병행 유지
3. 신규 인덱스의 차원·정규화·모델 계약 검증
4. 평가셋으로 검색 품질 비교
5. 검증 완료 후 원자적으로 Active Index 전환
6. 실패 시 기존 인덱스로 복귀

**2) 기본·Fallback Embedding의 품질**

Hash/Subword 기반 Fallback은 오프라인 동작과 기능 검증에 유용할 수 있지만 실제 의미 검색 품질을 보장하지 않는다. 기본 Provider 및 Fallback의 동작 조건을 사용자에게 명확히 알려야 한다.

**3) 대량 처리 안정성**

- Batch Embedding
- 중복 콘텐츠 Hash 기반 생략
- Incremental Reindex
- 중단 후 재개
- 메모리 및 배치 크기 제어
- 모델 다운로드 무결성 검증
- Index Build 진행률 및 취소
- 손상된 인덱스 복구

**권장 우선순위: P1**

---

## 4.4 Agent / Adaptive Harness

주요 경로:
- `rust/lattice-agent/`
- `rust/lattice-agent/src/kernel/`
- `rust/lattice-agent/src/kernel/probe.rs`
- `rust/lattice-agent/src/kernel/agentloop/`
- `rust/lattice-agent/src/kernel/agentloop/execution/`

### 잘한 점

- 모델 이름이나 파라미터 수만으로 능력을 단정하지 않고 Probe를 활용하는 방향
- Tool Ranking 및 실행 경로 선택
- JSON Repair와 실행 검증
- 모델별 실행 능력에 따른 Guided Mode
- 실행 결과 및 근거 검증을 Agent Loop에 포함하려는 설계

### 벤치마크 해석 시 주의

저장소의 `docs/ADAPTIVE_HARNESS_BENCHMARK.md`에 기록된 Qwen2.5 0.5B 기반 25개 태스크 결과는 초기 검증 자료로 의미가 있다. 다만 모델 하나, 태스크 25개, 분야별 소수 태스크에 기반한 결과이므로 범용 성능을 입증하는 자료로 확대 해석하면 안 된다.

문서에 기재된 Loopback Fixture 및 결정론적 UI Fixture 결과는 실제 인터넷·실제 OS 환경에서의 성공률과 구분해야 한다.

### 개선

- 0.5B/2B/7B/14B 이상 다양한 모델 규모 평가
- Qwen/Gemma/Llama 등 여러 모델 계열 비교
- Q4/Q5/Q8 양자화별 성능·정확도 측정
- 태스크 수 확대 및 반복 실행
- 실제 웹 환경과 실제 OS Computer Use 테스트 별도 운영
- Tool Execution Success와 Task Outcome Correctness 분리
- Answer Quality와 RAG Grounding을 독립 측정
- 실패 유형별 재현 가능한 테스트 케이스 보존
- Probe 비용과 실제 실행 성능의 상관관계 측정

### Agent Experience Memory 제안

Agent가 실패한 경험을 다음 실행에 재사용할 수 있도록 다음 정보를 구조화해 저장한다.

- 모델 및 런타임 버전
- Tool 및 입력 유형
- 실패 단계와 오류 코드
- 재시도 결과
- 성공한 복구 전략
- 해당 전략의 적용 범위와 신뢰도

단, 한 번의 실패를 일반 규칙으로 승격하지 말고 반복 관측과 평가를 거쳐 정책으로 반영해야 한다.

**권장 우선순위: P1**

---

## 4.5 Security / Privacy / Governance

주요 경로:
- `rust/lattice-agent/src/kernel/`
- `rust/lattice-host/src/gateway/`
- `rust/lattice-auth/`
- `SECURITY.md`
- `PRIVACY.md`

### 잘한 점

- Local-first 방향
- 사용자 승인 기반 실행 설계
- Workspace 경계 및 인증 계층
- Worker 접근 제한 방향
- 파일 변경과 실행 제안의 승인 흐름

### 개선 및 검증 필요 항목

| 영역 | 권고 |
|---|---|
| Prompt Injection | 문서·웹·메일의 지시문을 비신뢰 데이터로 격리 |
| Tool Permission | 도구별 최소 권한과 명시적 Scope |
| Workspace Isolation | API·검색·Graph·파일·Agent 전체 경로 검증 |
| Archive | ZIP Slip, Symlink, 압축 폭탄, 경로 우회 방어 |
| Secret | 로그·오류·진단 번들 민감정보 제거 |
| Network | SSRF 및 Loopback 접근 정책 검증 |
| Approval | 승인 후 대상 파일/리소스가 바뀌었는지 재검증 |
| Audit | 권한 변경, 데이터 변경, 도구 실행 이력 |
| Restore | 백업 파일 무결성 및 복구 후 권한 검증 |

특히 Prompt Injection 테스트는 악성 문서가 파일 삭제나 외부 전송을 지시하더라도 해당 내용을 단순한 비신뢰 데이터로 처리하고, 별도의 권한 및 사용자 승인 없이는 실행하지 않는지 검증해야 한다.

**권장 우선순위: P0/P1**

---

## 4.6 Backend / Rust Host / Python Worker

주요 경로:
- `rust/lattice-host/`
- `rust/lattice-host/src/gateway/`
- `latticeai/`
- `latticeai/worker_app.py`
- `latticeai/app_factory.py`

### 잘한 점

- 제품 Gateway와 AI Worker의 역할 분리
- Python Worker Route를 제한하고 Rust Host 중심으로 제품 경로를 관리하려는 방향
- API Schema 및 Fixture 동기화 구조

### 개선

**장애 복구**
- Worker Health Check
- Startup Readiness와 Liveness 구분
- Request Timeout
- Retry 및 Backoff
- Circuit Breaker
- Graceful Shutdown
- Host/Worker 버전 불일치 처리
- 작업 중복 실행 방지 및 Idempotency Key
- 취소된 작업의 자원 정리

**계약 및 호환성**
- OpenAPI/Schema 단일 기준 관리
- Contract Test
- 이전 버전 Client 호환성 검증
- Python-Rust Parity Test
- 오류 코드와 오류 메시지 표준화

**의존성**
- 실제 Worker 실행 경로에 필요한 Python 패키지만 유지
- 개발·테스트·배포 의존성 분리
- SBOM 및 취약점 점검

**권장 우선순위: P1/P2**

---

## 4.7 Frontend / React / TypeScript

주요 경로:
- `frontend/src/`
- `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/pages/`
- `frontend/src/features/`

### 잘한 점

- Lazy Loading 및 Route 단위 로딩
- React Query 기반 서버 상태 관리
- Zustand 기반 클라이언트 상태 관리
- Error Boundary
- i18n 구조
- Vitest 및 Playwright 테스트

### 개선

- App Root와 Route Registry 책임 분리
- Feature별 Layout 및 상태 경계 명확화
- API Hook과 Domain Logic 분리
- 긴 작업의 진행률·취소·재시도 UX 표준화
- Loading/Empty/Error/Partial Success 상태 일관화
- 접근성 및 키보드 탐색 테스트
- 한국어/영어 문자열 길이 차이와 레이아웃 검증
- 실제 기능 상태와 화면 표시의 불일치 방지

제품의 핵심 흐름은 다음처럼 일관되게 보여야 한다.

```text
Capture -> Understand -> Connect -> Retrieve -> Apply -> Learn
```

기능 수보다 사용자가 지식을 등록하고, 이해하고, 관계를 확인하고, 활용하는 흐름이 더 중요하다.

**권장 우선순위: P2**

---

## 4.8 Desktop / Tauri / CLI / Extensions

주요 경로:
- `src-tauri/`
- `bin/`
- `vscode-extension/`
- `browser-extension/`

### 개선

- Tauri에서 Host/Worker 중복 실행 방지
- 프로세스 종료 및 앱 재시작 복구
- 앱 업데이트 실패 시 롤백
- CLI의 오류 코드 및 자동 진단
- VS Code/Browser Extension의 연결 끊김 및 재연결
- 수집 실패 시 재시도 및 중복 방지
- 모든 Client의 공통 API Contract 유지
- Surface별 기능 지원 범위 명시

README와 Release Asset의 파일명·경로가 실제 배포 결과와 일치하는지 자동 검증하는 것도 필요하다. 문서에 기록된 설치 파일명과 실제 Asset 이름이 다르면 사용자가 설치 단계에서 혼란을 겪을 수 있다.

**권장 우선순위: P2**

---

## 4.9 CI/CD / Testing

주요 경로:
- `.github/workflows/ci.yml`
- `scripts/run_rust_tests.mjs`
- `scripts/benchmark_adaptive_harness.py`

### 확인된 좋은 방향

- Frontend, Python, Rust 테스트 분리
- Rust Core와 Runtime Job 분리
- Cargo Cache
- cargo-nextest 활용
- Clippy 및 rustfmt 검사
- Integration 및 Playwright Visual Test
- Release 전 검증 흐름

### 개선

**테스트 시간**
- 변경된 Crate 중심의 빠른 테스트 경로
- Unit / Integration / E2E 분리
- 테스트별 실행 시간 기록
- 느린 테스트 자동 식별
- 중복 빌드 및 불필요한 컴파일 제거
- PR용 Fast Gate와 Release용 Full Gate 구분

전체 검증을 제거하는 대신 개발 피드백 속도를 높이는 방향으로 최적화한다.

**플랫폼**
- Windows CI Matrix 확대
- macOS Intel/Apple Silicon 지원 범위 명확화
- 파일 경로, 프로세스, SQLite, 권한, 파일 감시 검증
- 실제 설치 패키지 기반 Smoke Test

**공급망 및 배포**
- SBOM 생성
- Artifact Signature
- Dependency Provenance
- Release Manifest
- 패키지 설치 후 Smoke Test
- 배포 실패 및 재시도 시 중복 Publish 방지

**권장 우선순위: P1/P2**

---

## 5. 우선순위별 개선 백로그

아래는 기능 추가가 아닌 신뢰성 및 품질 향상을 중심으로 정리한 실행 목록이다.

### P0 — 핵심 품질 게이트

| ID | 작업 | 완료 기준 |
|---|---|---|
| P0-01 | RAG Evaluation Dataset 구축 | 고정 질문·정답 근거·평가 기준 버전 관리 |
| P0-02 | Retrieval Benchmark | BM25F/Vector/GraphRAG/Hybrid 비교 자동화 |
| P0-03 | Answer Grounding 평가 | 답변 근거 및 Citation 정확도 측정 |
| P0-04 | Knowledge Conflict Test | 최신/과거/상충 지식 구분 검증 |
| P0-05 | Workspace Isolation Test | 검색·Graph·파일·Tool 교차 접근 차단 |
| P0-06 | Prompt Injection Test | 비신뢰 문서의 지시 실행 차단 |

### P1 — Brain 및 Agent 신뢰성

| ID | 작업 | 완료 기준 |
|---|---|---|
| P1-01 | Graph Schema Migration 검증 | 기존 DB Upgrade/Rollback 테스트 |
| P1-02 | Temporal Knowledge | 유효 기간과 변경 이력 검증 |
| P1-03 | Embedding Index 교체 | 무중단 전환 및 Rollback 검증 |
| P1-04 | Agent Multi-Model Benchmark | 다수 모델·규모·양자화 비교 |
| P1-05 | Agent Outcome Evaluation | Tool 성공과 최종 정답률 분리 |
| P1-06 | Worker Recovery Test | 종료·재시작·타임아웃 복구 |
| P1-07 | Rust/Python Contract Test | Schema 및 동작 불일치 검출 |

### P2 — 확장성 및 제품 완성도

| ID | 작업 | 완료 기준 |
|---|---|---|
| P2-01 | 대규모 Graph Benchmark | 10만/100만 노드 측정 |
| P2-02 | Incremental Index 최적화 | 변경분만 재색인 |
| P2-03 | Windows CI | 주요 사용자 시나리오 통과 |
| P2-04 | Desktop Lifecycle | 설치·업데이트·종료·복구 검증 |
| P2-05 | UI Surface Parity | Desktop/CLI/Extension 기능 차이 문서화 |
| P2-06 | SBOM 및 Artifact Signing | Release 산출물 검증 |

---

## 6. 권장 실행 로드맵

### Phase 1 — Brain Reliability

- Knowledge Graph 정확성
- Temporal Knowledge
- RAG Evaluation Framework
- Citation 및 Grounding 검증
- Embedding Index 무결성

**목표:** Brain이 잘못된 지식을 저장하거나 검색하는 문제를 측정하고 줄인다.

### Phase 2 — Agent Intelligence

- Adaptive Harness 다중 모델 평가
- Tool Failure Recovery
- Agent Experience Memory
- Multi-Agent Coordination
- 최종 결과물 품질 평가

**목표:** 도구를 실행하는 Agent를 넘어 근거 있는 결과를 만들어 내는 Agent로 발전한다.

### Phase 3 — Performance & Scalability

- 10만~100만 노드 벤치마크
- BM25F/Vector/Graph Expansion 프로파일링
- Incremental Indexing
- Memory 및 CPU Profiling
- p50/p95/p99 성능 추적

**목표:** 데이터 규모가 증가해도 검색과 인덱싱 성능을 예측하고 관리한다.

### Phase 4 — Platform Reliability

- Host/Worker 장애 복구
- Workspace 보안 검증
- Windows/macOS 통합 테스트
- Backup/Restore 검증
- Release Artifact 신뢰성

**목표:** 실제 사용자 PC에서 설치부터 복구까지 안정적으로 동작한다.

### Phase 5 — Digital Brain Intelligence

- 장기 경험 축적
- 지식 간 관계 자동 생성 및 검증
- 사용자 맥락 이해
- 지식 변경 감지 및 갱신
- 지식 기반 능동적 활용

**목표:** 특정 모델에 종속되지 않고 지속적으로 성장하는 Digital Brain을 구현한다.

---

## 7. 성능 및 품질 측정 대시보드 제안

성능 최적화는 감각이나 단일 테스트 시간만으로 판단하지 않는다. 버전별로 동일한 데이터와 환경을 사용해야 한다.

| 영역 | 측정 지표 |
|---|---|
| Ingestion | 문서/초, 파일별 처리 시간, 실패율 |
| Parsing | 문서 유형별 성공률, 추출 정확도 |
| Graph | Entity Precision/Recall, Relation Precision/Recall |
| Retrieval | Recall@K, MRR, nDCG |
| Grounding | Citation Precision, Evidence Coverage |
| Embedding | 처리량, 메모리, 인덱스 전환 시간 |
| Agent | Task Success, Outcome Correctness, Retry Rate |
| Host/Worker | 시작 시간, 장애 복구 시간, 오류율 |
| Frontend | 초기 로딩, 주요 작업 응답 시간 |
| Storage | DB 크기, 인덱스 크기, 백업/복구 시간 |

모든 성능 결과에는 다음 메타데이터를 함께 저장한다.

- Lattice AI 버전 및 Git Commit
- OS 및 CPU/메모리
- 모델 및 양자화 버전
- 데이터셋 버전과 크기
- 테스트 설정 및 반복 횟수
- 측정 일시

---

## 8. 최종 권고

Lattice AI의 차별성은 많은 모델을 연결하거나 Agent 기능을 많이 제공하는 데 있지 않다.

핵심은 다음 세 가지다.

1. **정확하게 기억하는 Brain:** 지식과 관계, 시간, 출처를 신뢰할 수 있게 관리한다.
2. **정확하게 검색하는 Brain:** 질문에 필요한 근거를 찾고 잘못된 지식을 배제한다.
3. **지식을 활용하는 Agent:** 근거를 사용해 실제로 올바른 결과를 만든다.

따라서 단기 개발 우선순위는 다음과 같이 권고한다.

```text
Knowledge Integrity
       |
       v
Retrieval Evaluation
       |
       v
Answer Grounding
       |
       v
Agent Outcome Quality
       |
       v
Performance & Platform Reliability
```

새 모델이나 기능을 추가하기 전에 위 항목을 측정할 수 있는 자동화된 품질 게이트를 구축하는 것이 가장 높은 투자 대비 효과를 낼 가능성이 크다.

**최종 판단:** 현재의 기술적 기반을 무너뜨리고 재작성하기보다, 기존 Rust Core·Hybrid Retrieval·Adaptive Harness를 유지하면서 평가 체계, 데이터 무결성, 장애 복구 및 실제 환경 검증을 강화하는 점진적 개선을 권고한다.

---

## 9. 참고 경로

주요 검토 대상 경로:

- `rust/lattice-core/`
- `rust/lattice-retrieval/`
- `rust/lattice-agent/`
- `rust/lattice-host/`
- `latticeai/`
- `frontend/src/`
- `src-tauri/`
- `vscode-extension/`
- `browser-extension/`
- `.github/workflows/ci.yml`
- `docs/ADAPTIVE_HARNESS_BENCHMARK.md`
- `scripts/run_rust_tests.mjs`

저장소: https://github.com/TaeSooPark-PTS/LatticeAI

---

*이 문서는 2026-11-20 기준 저장소 상태에 대한 정적 코드 리뷰 및 개선 제안이다. 각 항목의 완료 여부는 후속 구현과 자동화 테스트 결과로 별도 확인해야 한다.*
