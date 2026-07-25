# LatticeAI 전체 코드 리뷰 — 사용자 · 하네스 · 루프 · 인덱싱/Graph RAG

> **검토일:** 2026-07-25  
> **대상 릴리스:** **9.9.3 — Closed Loops** (`main`)  
> **관점:** (1) 사용자 경험 (2) 하네스 엔지니어링 (3) 루프 엔지니어링 (4) 파일·폴더·웹 인덱싱 → Graph → RAG  
> **성격:** 구현 PR이 아닌 **근거 기반 분석 + 다음 개선 로드맵**.  
> **선행 문서:**  
> - [`FULL_STACK_UX_HARNESS_KG_FILEGEN_REVIEW_2026-07-21.md`](FULL_STACK_UX_HARNESS_KG_FILEGEN_REVIEW_2026-07-21.md)  
> - [`REMAINING_WORK_AFTER_9.9.2.md`](REMAINING_WORK_AFTER_9.9.2.md) — 22개 항목 전부 9.9.3에서 출하  
> - [`V9.9.3_WORK_STATUS.md`](V9.9.3_WORK_STATUS.md), `RELEASE_NOTES_v9.9.3.md`

---

## 0. 한 줄 결론

9.9.3은 “열린 루프를 닫는” 릴리스로 성공했다.  
Artifact · Approval · First Value · Retrieval fusion · Folder watch · Grounding · File preview · agent_eval filegen 등이 **코드와 UI에 동시에** 존재한다.

다음 단계의 문제는 “기능을 더 넣는 것”이 아니라:

| 축 | 현재 (9.9.3) | 다음 도약 |
| --- | --- | --- |
| **사용자** | 루프가 *열리기* 시작함 | 루프가 *끊기지 않고* 재시작·재개·멀티서피스에서도 살아 있음 |
| **하네스** | 단일 에이전트·filegen·fusion 게이트가 강함 | 지속 상태·멀티에이전트·실모델 품질·E2E 지식 파이프라인을 **회귀 불가능**하게 |
| **루프** | PLAN→APPROVE→EXECUTE→VERIFY + 관측 | 컨텍스트 폭증·중간 진행 스트리밍·도구 경로 단일화·재검색 루프 |
| **Graph RAG** | 정직한 신호 + 클래스별 fusion | 청킹/추출/재인덱싱/인용 **품질 본체**를 한 단계 끌어올리기 |

---

## 1. 코드베이스 스냅샷 (근거)

### 1.1 규모와 경계

| 영역 | 대략 | 역할 |
| --- | --- | --- |
| `latticeai/` + `lattice_brain/` | ~67k LOC Python | API, agent, tools, Brain, KG, storage |
| `frontend/src/` | ~108 TS/TSX | Brain Home, Capture, Review, Act, onboarding |
| unit tests | ~153 파일 | 시나리오 중심 + fusion/filegen/approval 게이트 |
| 대형 모듈 | `app_factory` 1.3k, `workspace_os` 1.4k, `ingestion` 1.2k, `retrieval` 1.2k, `agent` 1.0k | 기여 비용·회귀 면적 |

**아키텍처 경계 (유지 강점):**

```text
UI (React / Tauri / VS Code / browser ext)
  → FastAPI sidecar (latticeai.app_factory + runtime stages)
    → services (chat, memory, search, ingestion, review, automation)
      → core (SingleAgentRuntime, ToolRegistry, tool_governor, file_generation)
      → lattice_brain (IngestionPipeline, KnowledgeGraphStore mixins, embeddings)
```

- `SingleAgentRuntime` — FastAPI/글로벌 없이 `AgentDeps` 포트만 사용  
- `IngestionPipeline` — file / folder / web text / chat / memory 단일 관문  
- `tool_governor` + `ChangeProposalService` — additive vs mutation 한 곳 분류  
- `LoopTrace` — 루프 관측의 기계 가독 측채널  

### 1.2 9.9.3에서 이미 닫힌 것 (재리뷰 시 “이미 됨”으로 취급)

이전 전체 리뷰(7/21)의 22개 백로그는 출하 완료로 본다. 대표:

| 영역 | 구현 위치 (대표) |
| --- | --- |
| 멀티파일 Artifact Loop | `file_generation.infer_project_manifest` / `validate_project_bundle` / `repair_bundle_references` |
| `awaiting_approval` + resume | `chat_agent_http._pause_for_approval`, `AgentApprovalCard` |
| First Value Loop | `POST /api/setup/demo-corpus`, `FirstValueLoopCard` |
| retrieval fusion + CI 게이트 | `lattice_brain/graph/fusion.py`, `test_retrieval_fusion_gate` |
| folder watch (opt-in) | `/api/ingestion/watch`, local KG watcher restore on lifespan |
| capture quality CTA | `capture_quality` / `assess_extraction_quality` |
| graph noise curate | `/knowledge-graph/curate/noise`, curator |
| grounding 배지 | `assess_answer_grounding`, chat trailer |
| file preview / job report / a11y / global DnD | frontend brain features |
| harness: agent_eval filegen, goldens, bench_models, pipeline E2E, funnel metrics | scripts + tests + `FunnelMetricsService` |
| PhaseBudgets, 확장자·ast 검증 | `agent.PhaseBudgets`, `file_generation.validate_file_content` |
| 모든 `write_file` sanitize | `_dispatch_step` → `sanitize_write_content` |

**본 리뷰의 P0는 “신뢰 구멍 메우기”가 아니라 “닫힌 루프를 운영·품질·규모에서 견고하게 만들기”다.**

---

## 2. 사용자 관점 리뷰

### 2.1 제품이 약속하는 네 번의 도파민 순간

| # | 순간 | 9.9.3 상태 | 남은 체감 갭 |
| --- | --- | --- | --- |
| 1 | **첫 저장** | 전역 DnD, Capture, 품질 경고 | 폴더 인덱싱 후 “뭐가 기억됐나” 요약은 job report로 개선됐으나, **장기 동기화 상태(watch 건강도)** 는 홈 1st viewport에 약함 |
| 2 | **첫 회상** | demo corpus 30초 트랙, grounding 배지, AnswerProof | 배지는 있으나 **클릭 → 원문 하이라이트 → 청크 위치** 까지 한 흐름으로 고정되지 않은 경우 있음 |
| 3 | **첫 산출물** | artifacts[] 카드, 인라인 preview, zip, repaired 배지 | 프로젝트 매니페스트가 **웹(html+css+js) 전용**; “React/Vite 앱”, “Python 패키지”는 agent 경로에 의존 |
| 4 | **첫 보호** | Review Center, 인라인 승인 카드, 409 rebase | 승인 상태가 **프로세스 메모리** — 서버 재시작/크래시 시 “승인하려다 사라짐” |

### 2.2 여정별 평가

#### A. 첫 실행 · 온보딩

**강점**

- device analysis → 모델 추천 → Brain Home 경로가 문서·코드 모두 “Brain first”로 정렬  
- First 5 minutes + First Value Loop가 “빈 그래프에 무엇을 해야 하지?”를 줄임  

**개선**

1. **Demo corpus 이후 내 자료로 전환 유도**  
   샘플 성공 직후 “지금 폴더 하나 연결하기 / PDF 하나 드롭” CTA를 동일 카드 안에서 다음 스텝으로 고정.  
2. **모델 없음 vs 모델 약함** UX 분리  
   no-model 정직한 empty는 이미 강함. “모델은 있지만 도구 JSON을 못 씀”은 filegen direct path가 커버하지만, agent 작업 실패 시 **왜 약했는지** (parse repairs 카운트)를 사용자 언어로 한 줄 노출하면 신뢰가 올라간다.  
3. **표면 간 패리티**  
   VS Code extension / browser capture / desktop shell이 Brain Home의 First Value·승인 카드와 **동일 계약**을 쓰는지 체크리스트화. 표면마다 “반쯤 되는 기능”이 남아 있으면 브랜드가 깨진다.

#### B. 일상 Brain Chat

**강점**

- action-aware chat: 일반 질문 vs 파일 생성 경로 분리  
- `context_quality` + `grounding` 이 응답 메타로 내려감  
- Agent 상태 노트: DONE / NEEDS_REVIEW / FAILED 구분  

**개선**

| 이슈 | 사용자에게 보이는 현상 | 권고 |
| --- | --- | --- |
| 근거 배지 ≠ 인용 강제 | “근거 없음”이어도 긴 자신감 있는 문장 | 시스템 프롬프트에 “출처 id를 괄호로 달기” + UI에서 unsupported면 톤 다운 배너 |
| 긴 agent 작업 침묵 | EXECUTE 중 타임라인 빈약 | transcript step을 스트리밍 이벤트로 노출 (읽기→쓰기→검증) |
| 승인 만료 | 10분 TTL 후 410 | 만료 전 경고 + “같은 계획으로 다시 계획만 세우기” 원클릭 |
| 생성 파일 Brain 재수집 | 백그라운드 성공/실패 비가시 | 카드에 “Brain에 기억됨 ✓ / 대기 중 / 실패” 상태 칩 |

#### C. 지식 수집 (파일·폴더·웹)

**강점**

- 품질 점수·저품질 경고·capture_quality suggestions  
- 폴더 job report, watch opt-in + 명시 동의  
- generated 파일 자동 ingest (`workspace://` provenance)  

**개선**

1. **Watch 모드 신뢰 UI**  
   마지막 스캔 시각, 대기 큐, 오류 3건을 홈 시그널에 고정. 폴링 기반이면 “실시간 아님”을 문구로.  
2. **웹 캡처 실패 복구**  
   thin capture 시 붙여넣기·재캡처 CTA는 있음 → 성공 후 **같은 쿼리로 바로 회상 테스트** 버튼을 붙이면 루프가 닫힘.  
3. **대용량 폴더**  
   첫 인덱싱 ETA / 처리량 / 스킵 비율을 프로그레스로. 사용자가 “멈춘 줄 알고” 재클릭하는 패턴 방지.

#### D. 검토 · 자동화 · Act

**강점**

- proposal-first, fail-closed overwrite  
- automation dry-run + last execution + 실패 시 Review 큐  

**개선**

- “제안만 쌓임” 체감 해소: Review 인박스 상단에 **오늘 처리 가능 건수 + 한 건 미리보기**  
- automation “지금 한 번 실행” 결과를 Brain Brief에 한 문장으로 고정 (이미 일부 존재 → empty/failed 시각 구분 강화)

### 2.3 사용성 휴리스틱 요약 (Nielsen 관점)

| 휴리스틱 | 평가 | 메모 |
| --- | --- | --- |
| 시스템 상태 가시성 | 좋음~우수 | agent 상태, context_quality, job report |
| 실세계 언어 | 좋음 | Review Center 인간 언어화 유지 |
| 사용자 통제·자유 | 좋음 | 승인/거절/rebase; 승인 지속성은 약함 |
| 일관성 | 양호 | 다중 표면·이중 승인 API 경로 주의 |
| 오류 예방 | 우수 | governor, sanitize, evidence-gated DONE |
| 인식 > 회상 | 양호 | First Value chips; 일상 추천 질문 강화 여지 |
| 유연성 | 양호 | Cmd+K, multi model |
| 미니멀 디자인 | 양호 | Brain first; 관리 기능은 2뎁스 |
| 오류 복구 | 양호 | NEEDS_REVIEW, rollback(git), 409 rebase |
| 도움말 | 보통 | 인앱 도움보다 문서 의존 — 인라인 “왜 막혔나” 강화 |

### 2.4 사용자 관점 — 우선 개선 (P1)

1. **승인 상태 영속화** (재시작에도 resume 가능)  
2. **에이전트 실행 중 실시간 step 타임라인**  
3. **근거 배지 → 출처 카드 클릭 → 청크/원문** 단일 플로우 고정  
4. **프로젝트 매니페스트 종류 확장** (최소: static site 외 plain multi-file Python/Node)  
5. **표면 패리티 체크리스트** (Desktop / Browser / VS Code)

---

## 3. 하네스 엔지니어링 관점

하네스 = *모델이 약해도 / 환경이 흔들려도 제품 계약이 깨지지 않게 가두는 장치*.

### 3.1 현재 하네스 지도

```text
                    ┌─────────────────────────────┐
  scripted LLM  ──► │ SingleAgentRuntime + deps   │
                    │ LoopTrace / PhaseBudgets    │
                    └─────────────┬───────────────┘
                                  │
         agent_eval (23 scenarios, CI gate)
         artifact write scenarios + filegen goldens
         retrieval fusion gate (recall@5, must-include, class accuracy)
         knowledge pipeline E2E (temp folder → hybrid → quality)
         product_readiness evidence links
         funnel_metrics (local JSON, advisory)
         bench_models --filegen (fail-open, real models)
```

**교과서적으로 강한 부분**

| 장치 | 왜 강한가 |
| --- | --- |
| `AgentDeps` 포트 | I/O 없는 상태머신 → 결정론 시나리오 |
| `extract_action_details` repairs | weak-model 관용을 **측정 가능**하게 |
| `sanitize_write_content` on all writes | direct path와 agent path 계약 통일 |
| DONE = PASS ∧ evidence | 가짜 성공 제거 |
| critic unparseable → NEEDS_REVIEW | fail-closed 검증 |
| `MUTATING_TOOL_INVENTORY` + assert coverage | 신규 mutator 미분류 시 CI 실패 |
| fusion fact-class byte-compat | 튜닝이 기본 경로를 깨지 않음 |
| golden dirty→clean | 프롬프트 회귀를 바이트로 잡음 |

### 3.2 하네스 약점 / 사각지대

| ID | 약점 | 리스크 | 권고 |
| --- | --- | --- | --- |
| H1 | **승인 상태가 in-process dict** (`chat_agent_http._approvals`) | 재시작 시 resume 404; 멀티워커/다중 프로세스 불가 | SQLite/JSONL run store + token hash; 재개 계약 테스트 |
| H2 | **이중 hybrid** — `SearchService.hybrid_search` (3채널) vs `KG.hybrid_search` (lexical+vector α) | 가중치·query_class가 표면마다 미묘히 다름 | 단일 `RetrievalPolicy` 모듈 + 계약 테스트 “같은 쿼리 → 같은 class/weights 문서화” |
| H3 | **개념 추출 LLM 경로** (`_llm_extract_concepts`) | 네트워크/모델에 따라 그래프 토폴로지 비결정 | CI는 규칙 폴백만; LLM 경로는 별도 opt-in eval + 스키마 검증 |
| H4 | **멀티에이전트 / workflow designer** | single-agent 대비 시나리오 밀도 낮음 | 최소 5시나리오: pause/resume, fail node, rollback, permission deny |
| H5 | **funnel_metrics가 advisory only** | 제품 회귀가 메트릭만 악화돼도 CI 무반응 | weekly 리포트 + 선택적 soft threshold (예: code_only rate) |
| H6 | **임베딩 모델 교체** | 벡터 인덱스 stale, hybrid이 lexical로 폴백해도 “hybrid”처럼 보일 수 있음 | embedder fingerprint in index_status; mismatch 시 명시 `stale_embedder` |
| H7 | **실모델 agent 루프** | bench_models는 filegen 중심 | 주간 “agent 5 tasks × models” smoke (fail-open) |
| H8 | **프론트 E2E** | unit/visual 대비 실제 sidecar 연동 여정 부족 | Playwright: demo corpus → 질문 → grounding → HTML 생성 1경로를 CI 옵션 잡 |

### 3.3 하네스 성숙도 점수 (주관, 상대)

| 영역 | 점수 /5 | 근거 |
| --- | --- | --- |
| 단일 에이전트 루프 | 4.5 | 관측+eval+fail-closed 거의 완비 |
| 파일 산출물 | 4.5 | sanitize/golden/manifest |
| 검색/fusion | 4.0 | 게이트 있음; 이중 레이어 |
| 수집 파이프라인 | 3.5 | E2E 1경로; 바이너리/OCR 약함 |
| 승인/거버넌스 지속성 | 3.0 | 논리 강 / 저장 약 |
| 멀티에이전트·자동화 | 3.0 | 기능 있음 / 하네스 얇음 |
| UX 퍼널 | 3.0 | 수집됨 / 아직 제품 아님 |

### 3.4 하네스 — 다음 구현 순서

```text
Wave H-A (신뢰 지속성)
  1. Durable approval/run store
  2. Embedder fingerprint + rebuild signal
  3. RetrievalPolicy 단일화 + 계약 테스트

Wave H-B (회귀 면 확장)
  4. Multi-agent/workflow 5 scenarios
  5. Playwright first-value E2E (optional CI)
  6. Weekly real-model agent smoke

Wave H-C (관측 → 의사결정)
  7. Funnel soft gates + dashboard endpoint
  8. LoopTrace → 모델별 repair rate 리포트
```

---

## 4. 루프 엔지니어링 관점

### 4.1 현재 단일 에이전트 루프

```text
IDLE
  → PLANNING          (planner JSON, normalize_plan, PhaseBudgets.plan_tokens)
  → WAITING_APPROVAL  (approval_requirements / pause_for_approval OR approve)
  → EXECUTING         (tool JSON, governor, sanitize write_file, loop guard)
  → VERIFYING         (critic; fail-closed; evidence gate)
      ↻ EXECUTING     (corrections, max_retry)
      → ROLLBACK      (git rollback port → FAILED)
  → DONE | FAILED | NEEDS_REVIEW
  → memory_update     (background, filter_learnings)
```

**잘 설계된 불변식 (유지 필수)**

1. 스코프 도구의 `workspace_id` / `user_email`은 **서버 소유** (`SCOPED_KNOWLEDGE_TOOLS`)  
2. DONE = 파싱된 PASS **그리고** 실행 evidence  
3. destructive는 agent 모드 하드 블록  
4. mutation은 proposal 또는 fail-closed  
5. 모든 관용 파싱은 `repairs`로 기록  

### 4.2 루프 구조 이슈

#### L1. 승인 경로 이중화

- 레거시: `human_in_loop` → `waiting_approval` + `context_id`  
- 신규: `awaiting_approval` + `run_id` + `approval.token`  
- 순수 `approve(approved_by_human=False)` 는 여전히 **FAILED**로 끝남 (HTTP가 미리 pause)

**위험:** 어댑터/테스트/다른 엔트리포인트가 `approve`만 호출하면 UX 계약이 깨짐.  
**권고:** runtime에 `pause_or_approve()` 단일 진입; HTTP는 thin wrapper. 상태 enum과 API status 문자열 매핑 표 하나로 문서화.

#### L2. Executor 컨텍스트 O(transcript) 폭증

`_executor_context`가 plan + recent chat + **전체 transcript JSON**을 매 step 주입.

- 긴 작업에서 토큰 소진 → PhaseBudgets.execute_tokens와 충돌  
- 약한 모델은 오래된 step에 주의 분산  

**권고:**

- sliding window: 최근 N step + 요약 1블록  
- 도구 결과 content 본문은 transcript에 해시/길이만, 필요 시 re-read  
- corrections는 최신 3개만  

#### L3. 중간 관측 ≠ 사용자 스트림

`LoopTrace` / transcript는 API 응답·평가용으로 강하지만, 실행 중 **SSE/websocket step 이벤트**는 제품 루프의 체감 핵심.

**권고:** `on_step` 콜백 포트를 `AgentDeps`에 추가 (테스트는 recorder). UI는 “3/7 쓰기 완료”.

#### L4. Artifact 검증이 critic과 분리

sanitize는 구조적 유효성(HTML 앵커, ast.parse 등)을 보장.  
critic은 자연어 판정. **“요청한 기능이 들어갔는가”** 는 둘 다 약함.

**권고 (가벼운 결정론 체크리스트):**

- HTML: 필수 키워드/섹션 (user_request 토큰 일부 포함 여부)  
- bundle: 로컬 ref 전부 resolve (이미 validate_project_bundle)  
- critic 프롬프트에 “artifacts[] valid/repaired 필드를 확인하라” 명시  

#### L5. 실행 중 RAG 미갱신

파일을 쓴 뒤 Brain에 ingest 되어도, 같은 run의 다음 step은 **옛 recent_chat_context**만 봄.  
“만들고 이어서 설명해” 류 멀티턴 agent 품질이 떨어짐.

**권고:** write 성공 후 optional `refresh_workspace_snapshot()` 포트; 또는 executor에 “방금 쓴 경로 목록” 섹션.

#### L6. Memory 루프 품질

`filter_learnings`로 trivial 억제는 됨.  
여전히:

- 실패 학습이 약함 (FAILED/NEEDS_REVIEW 도 짧게 기록?)  
- brain_memory 없을 때 vault dump 경로 이원화  

**권고:** 터미널 상태별 learning 정책 테이블; 항상 동일 ingestion 경로.

#### L7. Rollback 범위

git rollback만. git 없는 workspace / binary 생성물은 복구 메시지뿐.

**권고:** proposal 적용 전 스냅샷(이미 일부 존재)과 agent rollback 정책 정렬; “rollback: none | git | snapshot”.

#### L8. 프로젝트 매니페스트 vs agent 계획

direct path의 `infer_project_manifest`는 결정론·안전.  
agent planner가 같은 요청을 여러 `write_file`로 쪼개면 **경로·품질 정책이 갈림**.

**권고:** planner normalize 단계에서도 manifest 추론 성공 시 steps를 매니페스트로 rewrite (heuristic_file_step과 대칭).

### 4.3 루프 품질 지표 (측정 제안)

| 지표 | 정의 | 목표 (초안) |
| --- | --- | --- |
| parse_recovery_rate | recovered parse / all parse_errors | 모델별 추적, 회귀 감지 |
| repair_density | repairs / llm_calls | 약모델 ↑ 정상; 갑작스런 급증 알림 |
| evidence_pass_rate | DONE / (DONE+NEEDS_REVIEW) | 안정 구간 유지 |
| approval_resume_success | resume 2xx / pause | ≥ 0.95 (영속화 후) |
| steps_to_done | median execute steps | 작업 클래스별 베이스라인 |
| context_tokens_p95 | executor prompt size | 윈도우 도입 후 ↓ |

### 4.4 루프 — 우선 개선 (P1)

1. Transcript sliding window + tool result truncation  
2. `AgentDeps.on_step` 스트리밍 포트 + UI 타임라인  
3. 승인 진입 API/런타임 단일화 + durable store  
4. Plan normalize 시 project manifest rewrite  
5. Critic ↔ artifact meta 연동 체크리스트  

---

## 5. 파일 · 폴더 · 웹 → 인덱싱 → Graph → RAG

### 5.1 파이프라인 실측 맵

```text
[Sources]
  local file/folder  ──┐
  browser capture    ──┼─► IngestionPipeline.ingest(IngestionItem)
  chat / memory      ──┤         │
  generated artifacts──┘         ▼
                          quality_gate (observe-only)
                          assess_extraction_quality
                          dispatch_tool(kg_ingest.*)
                                 │
                                 ▼
                    KnowledgeGraphIngestMixin
                      nodes / edges / chunks
                      concepts & triples (LLM→rules)
                      provenance record
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
            vector index   discovery    curator noise job
            (incremental)  index        (opt-in dry-run)
                    │
                    ▼
            Retrieval
              lexical (FTS + LIKE fallback + topic boost)
              vector (embeddings)
              graph channel (SearchService)
              fusion_profile(query_class)
                    │
                    ▼
            Chat / Docgen context
              context_quality_signal
              assess_answer_grounding
              AnswerProof UI
```

### 5.2 단계별 평가

#### S1. Capture / Extract

| 항목 | 상태 | 코멘트 |
| --- | --- | --- |
| 텍스트 파일 | 강함 | 품질 점수, 청크, 해시 중복 |
| 폴더 | 강함 | ignore, background job, report, watch |
| 웹/브라우저 | 중~강 | upstream 추출 품질 의존; thin CTA 있음 |
| PDF/오피스/이미지 | 중 | 텍스트 못 보면 score 0.5 placeholder 가능 |
| 생성 파일 재수집 | 강함 | fail-open, env로 끄기 |

**개선**

- MIME별 extractor 성공률 메트릭  
- PDF: 페이지 단위 청크 + 페이지 번호 메타 (인용 UI 핵심)  
- 이미지: OCR 실패를 low quality로 명시 (이미 철학과 맞음)

#### S2. Chunking

`_chunks(text, size=1200, overlap=160)` — 고정 문자 윈도우.

**한계**

- 코드: 함수 경계 무시 → 검색 노이즈  
- 마크다운: 헤딩 단위가 더 나음  
- 한국어: 문자 수 ≈ 토큰 수 아님  

**권고**

- `chunk_strategy: plain | markdown | code` (확장자 라우팅)  
- 청크 메타에 `start_char`, `heading_path`  
- 전략 변경 시 content_hash 재계산 → 재인덱싱 잡

#### S3. Entity / Relation extraction

`_extract_concepts` / `_extract_triples`: LLM 우선, 규칙 폴백.  
Curator + noise job으로 DF 컷·동사 정규화.

**한계**

- 규칙 폴백은 한글 조사 기반 일반 명사 노이즈 가능 (blacklist로 완화 중)  
- 관계 동사가 문장 패턴에 묶여 **의미 그래프보다 동시발생 그래프**에 가까울 수 있음  
- 사용자 확인 없는 auto-promote

**권고**

1. Concept confidence + source span 저장  
2. 노이즈 curate를 주기 잡(옵트인)으로 기본 제안  
3. Person/Decision 등 고가치 타입은 승격 임계값 상향  
4. “그래프에 보이기 전 검토” 모드 (enterprise)

#### S4. Indexing (vector)

- 증분 `index_node_incremental`, 실패 시 pending + backlog  
- auto vector env 플래그  

**개선**

- embedder model id / dim fingerprint  
- 부분 실패 재시도 큐 (exponential backoff)  
- 대량 폴더 첫 인덱싱 시 batch embed (throughput)

#### S5. Retrieval / Fusion

**강점**

- query class: fact | code | person | recency  
- 채널 가중치 env 오버라이드  
- CI 벤치 임계값  
- workspace scoping fail-closed  

**약점**

| 항목 | 설명 |
| --- | --- |
| 이중 fusion API | 서비스 3채널 vs 그래프 2채널 α |
| 쿼리 재작성 부재 | 구어체/대명사 질의 약함 |
| multi-query / HyDE 없음 | 약모델 임베딩 품질 보완 수단 부재 |
| 재순위(cross-encoder) 없음 | top_k 정밀도 천장 |
| 시간 감쇠 | recency class 가중만; 스코어에 age decay 약함 |

**권고 (현실적 순서)**

1. RetrievalPolicy 단일화  
2. 간단한 query rewrite (규칙: 최근/코드/파일명 힌트 정규화) — LLM rewrite는 옵션  
3. age decay: `score *= exp(-λ Δt)` for recency class  
4. (나중) cross-encoder rerank 옵션 — 로컬 소형 모델  

#### S6. Context assembly → Answer

- `context_builder.retrieve_context_for_generation` — multi-hop + markdown sections (문서 생성에 강함)  
- 채팅 경로는 memory/search 서비스 쪽 hybrid + quality/grounding  

**갭:** docgen 컨텍스트 파이프라인과 chat 컨텍스트 파이프라인이 **공유 모듈이 약함**.  
같은 Brain인데 문서 생성과 채팅 회상 품질이 달라질 수 있음.

**권고:** `BrainContextAssembler` 하나로 sources[], markdown, quality, grounding 입력 공유.

#### S7. Grounding / Citations

`assess_answer_grounding` — 토큰 겹침 + 제목 명시 인용. Annotation only.

**한계**

- 요약형 답이 원문 단어를 안 쓰면 false “근거 없음”  
- 반대로 일반 단어 겹침으로 false “근거 있음” (stop token으로 완화)  
- 답변 생성 시 **출처 강제 프롬프트**와 분리되어 있음  

**권고**

1. 프롬프트: “각 주장 끝에 [n]”  
2. UI: supported source 카드 클릭 → 청크  
3. grounding eval 세트를 fusion gate 옆에 추가 (synthetic)

### 5.3 보안 · 스코프 · 프라이버시 (인덱싱 교차)

- workspace filter fail-closed: 우수  
- secret 패턴 마스킹 (curator): 유지·확장 (Slack/GitHub 패턴 있음)  
- folder watch 명시 동의: 필수 유지  
- 생성 파일 자동 ingest: 민감 경로 제외 목록 점검  

### 5.4 Graph RAG — 우선 개선 (P1)

1. **타입별 청킹 전략** (md/code/plain)  
2. **RetrievalPolicy 단일화** + chat/docgen assembler 공유  
3. **Embedder fingerprint / rebuild UX**  
4. **인용 프롬프트 + grounding 벤치**  
5. **바이너리/PDF 추출 품질 정직 신호 강화** (페이지 단위)

---

## 6. 교차 이슈 (세 관점이 겹치는 곳)

| 이슈 | 사용자 | 하네스 | 루프 | Graph RAG |
| --- | --- | --- | --- | --- |
| 승인 비영속 | 재시작 시 작업 유실 | 재개 테스트 불가 | WAITING 상태 저장 필요 | — |
| 이중 retrieval | “어떨 땐 잘 찾음” | 게이트가 한 API만 볼 수 있음 | agent 도구 검색 경로 | fusion 불일치 |
| transcript 폭증 | 긴 작업 실패 | eval 토큰 비대 | execute 품질 | — |
| 매니페스트 범위 | 웹만 잘 됨 | filegen 시나리오 편중 | plan rewrite 필요 | 생성물 재인덱싱 OK |
| 휴리스틱 개념 | 그래프 지저분 | 비결정 | — | curate 주기화 |

---

## 7. 전체 코드 품질 (구조)

### 7.1 강점 (유지)

- DI / 포트 / mixin 분해 방향이 AGENTS.md와 일치  
- fail-closed · 정직한 degraded mode 문화  
- 문서·릴리스 게이트 (`check_current_release_docs`, openapi drift, i18n)  
- proposal-first 거버넌스가 제품 신뢰의 중심  

### 7.2 구조 부채

| 모듈 | LOC대 | 메모 |
| --- | --- | --- |
| `app_factory.py` | ~1.3k | runtime stage로 많이 빠졌으나 여전히 조립 밀도 높음 |
| `workspace_os.py` | ~1.4k | 권한·경로·도구 경계 — 추가 분해 후보 |
| `ingestion.py` | ~1.2k | 품질/잡/파이프라인 공존 — job/watch 서브모듈 후보 |
| `retrieval.py` | ~1.2k | search/graph/list 혼재 — 이미 vector/docgen 분리됨, list/graph 추가 분리 가능 |
| `_kg_common.py` | ~0.7k | 추출·청크·유틸 집중 — chunk/extract 모듈 분리 시 테스트 용이 |
| 승인 API 이중 경로 | — | 레거시 human_in_loop vs awaiting_approval |

**원칙:** 동작 보존 + 이동 우선 (AGENTS 리팩터 규칙). 대규모 rewrite 금지.

### 7.3 테스트 문화

- 시나리오 이름·결정론 게이트: 모범  
- 다음: **상태 지속성**, **멀티 워커**, **실모델 smoke**, **프론트 E2E 1황금 경로**

---

## 8. 개선 로드맵 (실행 가능)

### Wave 0 — 계약 고정 (1주 내, 저위험 고레버리지)

| # | 항목 | 성공 기준 |
| --- | --- | --- |
| 0.1 | 승인/run durable store | 서버 재시작 후 유효 토큰 resume 성공 테스트 |
| 0.2 | RetrievalPolicy 단일 모듈 | SearchService·KG·chat_helpers가 동일 class/weights 문서 |
| 0.3 | Executor transcript window | p95 프롬프트 크기 감소 + agent_eval 100% 유지 |
| 0.4 | Plan←manifest rewrite | “html+css+js” agent 경로도 bundle 검증 통과 |

### Wave 1 — 사용자 체감 (1–2주)

| # | 항목 | 성공 기준 |
| --- | --- | --- |
| 1.1 | Step 타임라인 스트리밍 | 실행 중 3개 이상 step UI 갱신 |
| 1.2 | 출처 카드 → 청크 점프 | grounding supported 시 1클릭 원문 |
| 1.3 | Watch/job 건강 시그널 홈 노출 | 마지막 동기화·에러 샘플 |
| 1.4 | 표면 패리티 매트릭스 | Desktop/Browser/VS Code 기능표 + 갭 이슈 |

### Wave 2 — Graph RAG 품질 (2–3주)

| # | 항목 | 성공 기준 |
| --- | --- | --- |
| 2.1 | md/code 청킹 | 코드 질의 recall 벤치 향상 (게이트 상향 가능 수준) |
| 2.2 | Embedder fingerprint | 모델 바꾸면 UI/API가 stale 명시 |
| 2.3 | 인용 프롬프트 + grounding bench | synthetic set 임계값 |
| 2.4 | PDF 페이지 메타 | 인용에 page 표시 |
| 2.5 | 주기적 noise curate 제안 | dry-run 리포트 → 1클릭 적용 |

### Wave 3 — 하네스 확장

| # | 항목 | 성공 기준 |
| --- | --- | --- |
| 3.1 | Workflow/multi-agent 5시나리오 | CI 게이트 |
| 3.2 | Playwright first-value E2E | nightly 또는 optional CI |
| 3.3 | Funnel soft gate | code_only rate 이상 시 경고 |
| 3.4 | 실모델 agent weekly smoke | 리포트 아티팩트 |

### Wave 4 — 범위 확장 (제품 선택)

- 매니페스트: Python package / Vite React 템플릿  
- 쿼리 rewrite / rerank  
- 승인 TTL UX (만료 전 경고, 재계획)  
- Enterprise: 그래프 승격 인간 검토 모드  

---

## 9. 의도적 비목표 (이번 리뷰 기준)

- 클라우드 전용 벡터 DB 강제 (로컬 퍼스트 유지)  
- 답변 차단형 강제 grounding (현재 annotation 철학 유지; 톤 다운은 OK)  
- 전체 monorepo 마이크로서비스 분해  
- 파괴적 KG 마이그레이션  
- 패키지 퍼블리시/프로덕션 배포 (사용자 요청 전까지)

---

## 10. 권고 요약 (Top 10)

1. **승인/run 상태 영속화** — 닫힌 루프의 가장 큰 운영 구멍  
2. **Executor 컨텍스트 윈도우** — 긴 작업 안정성  
3. **RetrievalPolicy 단일화** — 검색 체감 일관성  
4. **Step 스트리밍 UI** — 루프의 사용자 가시성  
5. **출처 클릭 → 청크** — RAG 신뢰의 완성  
6. **타입별 청킹** — 검색 품질 본체  
7. **Embedder fingerprint** — hybrid 정직성  
8. **Plan manifest rewrite** — 파일 루프 경로 단일화  
9. **멀티에이전트 하네스** — 다음 기능 폭발 전에 가드  
10. **표면 패리티** — “앱마다 다른 Lattice” 방지  

---

## 11. 모듈 레퍼런스 (리뷰가 본 핵심 파일)

| 관심사 | 경로 |
| --- | --- |
| Agent loop | `latticeai/core/agent.py`, `agent_trace.py`, `agent_eval.py` |
| Governance | `latticeai/core/tool_governor.py`, `tool_registry.py` |
| File artifacts | `latticeai/core/file_generation.py`, `services/tool_dispatch.py` |
| Agent HTTP / approval | `latticeai/api/chat_agent_http.py` |
| Chat grounding | `latticeai/api/chat_helpers.py`, `chat_stream.py` |
| Ingestion | `lattice_brain/ingestion.py` |
| Graph write | `lattice_brain/graph/ingest.py`, `write_master.py` |
| Retrieval | `lattice_brain/graph/retrieval.py`, `retrieval_vector.py`, `fusion.py` |
| Search fusion | `latticeai/services/search_service.py` |
| Context/docgen | `latticeai/core/context_builder.py` |
| Curator | `lattice_brain/graph/curator.py`, `proactive.py` |
| Funnel | `latticeai/services/funnel_metrics.py` |
| Frontend loops | `features/brain/AgentApprovalCard.tsx`, `FirstValueLoopCard.tsx`, `FilePreviewModal.tsx`, `hooks/useBrainChat.ts` |

---

## 12. 결론

LatticeAI 9.9.3은 **로컬 우선 Digital Brain**으로서, 에이전트 루프·거버넌스·지식 파이프라인·평가 문화가 한 제품 안에 정합적으로 묶여 있는 드문 코드베이스다.  
“Closed Loops”는 이름이 아니라 실제다 — 다만 그 루프의 **지속성(persistence), 일관성(single policy), 가시성(streaming), 검색 본체 품질(chunk/embed/cite)** 은 다음 릴리스의 전장이다.

다음 릴리스 테마 제안:

> **9.10.x — Durable Loops**  
> 승인과 실행이 재시작을 이기고, 검색 정책이 하나이며, 사용자가 루프 중간을 보고, 인용이 원문까지 닿는다.

---

*본 문서는 `docs/reviews/` 아카이브 정책에 따라 역사적 리뷰로 유지한다. 버전 번호가 후속 릴리스에서 등장해도 본문의 9.9.3 스냅샷 서술은 재작성하지 않는다.*
