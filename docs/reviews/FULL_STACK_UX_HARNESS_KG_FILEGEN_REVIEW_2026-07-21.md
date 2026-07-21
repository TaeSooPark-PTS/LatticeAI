# LatticeAI 전체 코드 리뷰 — UX · 하네스 · 루프 · Graph/RAG · 파일 생성

> **검토일:** 2026-07-21  
> **대상 릴리스:** **9.9.1 — Clean Foundations** (`main`)  
> **목적:** 사용자가 “확 와닿는” LatticeAI가 되기 위한 제품·UX·하네스·루프·지식 파이프라인·모델 불문 파일 생성 개선 방향을 **근거 기반**으로 정리한다.  
> **성격:** 구현 PR이 아니라 **분석 + 우선순위 로드맵** 문서. 이전 리뷰(특히 `CODE_REVIEW_2026-07-20.md`, `USABILITY_AUDIT.md`, `PRODUCT_DIRECTION_REVIEW.md`)의 P0 다수는 9.9.0/9.9.1에서 이미 닫혔으며, 본 문서는 **그 이후의 다음 격차**에 초점을 둔다.

---

## 0. 한 줄 결론

LatticeAI는 이미 **로컬 우선 Digital Brain**으로서 제품 골격·거버넌스·관측 가능성·평가 문화가 매우 강한 코드베이스다.  
다음 도약은 “기능을 더 넣는 것”이 아니라, 아래 네 축을 **사용자가 체감하는 완성도**로 끌어올리는 일이다.

| 축 | 현재 수준 | 다음 목표 |
| --- | --- | --- |
| **제품 감각 (와닿음)** | Brain 메타포 + First 5 minutes + Review Center 언어화 | “넣으면 바로 써지는 기억” 30초 체험, 성공 피드백이 시각적으로 폭발 |
| **UX polish** | i18n 오류, Cmd+K, 제안 카드, 코드 블록 Save | 파일 생성 → 미리보기/다운로드/Brain 재인덱싱까지 원클릭 루프 |
| **하네스 / 루프** | LoopTrace, agent_eval, governor, fail-closed | 약한 모델 전용 **Artifact Loop**, 다단계 파일 프로젝트 생성 루프 |
| **File→Folder→Web→Graph→RAG→Automation** | 정직한 품질 신호·백그라운드 폴더 잡·automation suggestions | **연속 동기화 + 검색 품질 튜닝 + 자동화가 실제로 도는 체감** |

---

## 1. 코드베이스 스냅샷 (근거)

### 1.1 규모와 경계

| 영역 | 대략 규모 | 핵심 역할 |
| --- | --- | --- |
| `latticeai/` | ~45.7k LOC (Python) | FastAPI API, agent 루프, 도구, 서비스, 설정 |
| `lattice_brain/` | ~17.7k LOC | KG, ingestion, retrieval, vector, memory, portability |
| `frontend/src/` | ~38k LOC (TS/TSX) | Brain home, capture, review, command palette, admin |
| unit tests | ~140 파일 | 시나리오 중심 이름 (9.9.1 정리) |

**아키텍처 경계 (강점):**

- `latticeai.core.agent.SingleAgentRuntime` — 순수 상태머신 + `AgentDeps` 포트 (I/O 없음)
- `latticeai.core.tool_registry` / `tool_governor` / `ChangeProposalService` — 도구 리스크·변경 클래스·제안 경로
- `lattice_brain.ingestion.IngestionPipeline` — 단일 수집 관문 (file / folder / web text / chat / memory)
- `lattice_brain.graph.*` — schema / ingest / retrieval / vector / projection 분해
- React Brain surface + Tauri shell + VS Code extension — 동일 sidecar 위에 다중 표면

### 1.2 이미 닫힌 신뢰 격차 (9.9.0–9.9.1)

이전 전체 리뷰(`CODE_REVIEW_2026-07-20.md`)의 핵심 P0는 9.9.0에서 해결됨:

- Proposal `base_sha256` + 승인 시 conflict (사용자 편집 덮어쓰기 방지)
- Critic 파싱 실패 → `NEEDS_REVIEW` (가짜 PASS 제거)
- `MUTATING_TOOL_INVENTORY` + 미지원 overwrite fail-closed
- 온보딩 device analysis `loading | ready | unavailable`
- 9.9.1: legacy root shim 제거, First 5 minutes, Review Center 인간 언어, 로컬라이즈 에러

**따라서 본 리뷰의 P0는 “보안/신뢰 구멍”보다 “제품 체감·파일 완성도·지식 파이프라인 강도”에 둔다.**

### 1.3 제품 약속 vs 구현 매핑

| 약속 (README / AGENTS) | 구현 위치 | 체감 갭 |
| --- | --- | --- |
| 모델은 갈아타도 Brain은 남는다 | Brain archive, scoped memory, model router | 첫 회상 성공까지 경로가 길 수 있음 |
| 파일/폴더/웹 → 그래프 | `IngestionPipeline`, browser extension, folder jobs | 웹 추출 품질 변동, 폴더 동기화 “완료 후 뭐가 달라졌나” 약함 |
| 안전한 자동화 | change proposals, automation drafts | “제안만 쌓이고 실행 피드백이 약함” |
| 어떤 모델이든 파일 생성 | `file_generation.py` + chat intent direct path | 단일 파일은 강함, **멀티파일 프로젝트 / 에이전트 JSON 루프**는 약함 |

---

## 2. 전체 코드 품질 리뷰

### 2.1 강점 (유지·확장할 것)

#### A. 런타임 경계와 테스트 가능성

`SingleAgentRuntime`은 FastAPI/글로벌 없이 `AgentDeps`만 받는다. 그 결과:

- `scripts/agent_eval.py` + `latticeai/core/agent_eval.py` 결정론 시나리오
- weak-model gauntlet (think strip, fence, trailing comma, python literal)
- loop detection, correction escalation, destructive block, critic retry

이는 **하네스 엔지니어링의 교과서적 패턴**이다. 루프 변경 시 이 게이트를 깨면 릴리스가 막힌다.

#### B. 변경 거버넌스 (proposal-first)

`tool_governor.classify_tool_call`이 additive / mutation / destructive / exec를 한 곳에서 분류하고,  
`PROPOSAL_CAPABLE_TOOLS = {write_file, edit_file}` 외 overwrite는 fail-closed.

사용자 신뢰의 핵심 계약이다. UX는 이미 Review Center에서 위험·변경 클래스를 인간 언어로 번역한다.

#### C. 모델 불문 파일 생성 파이프라인 (v9.2.0 이후)

`latticeai/core/file_generation.py`는 약 450줄의 **순수 파이프라인**:

1. **Prompt** — 확장자별 규칙 + 첫 줄 앵커 (`<!DOCTYPE html>` 등)
2. **Extract** — think block / fence / HTML·JSON 슬라이스 / 채팅 잡음 제거
3. **Validate** — HTML 완결성, JSON parse, CSS 규칙, refusal 감지
4. **Retry** — 한 번의 corrective attempt
5. **Repair** — 결정론 scaffold (항상 유효한 파일 보장)

`chat_intents.direct_file_action` + `chat_helpers.is_file_action_request` / `infer_file_target`로  
“html 파일 만들어줘” 같은 요청이 **JSON tool 루프를 우회**해 direct path로 들어간다.

프론트는 `useBrainChat`의 `onAgent`에서 `created_files`를 메시지에 붙이고,  
`MessageMarkdown`은 코드 블록에 **Save as file**을 제공한다.

#### D. 정직한 지식 파이프라인 (v9.8.0)

- 추출 품질 점수 (`assess_extraction_quality`)
- 재개 가능 폴더 잡 + jobs API
- `context_quality_signal` (hybrid / lexical_only / none, limited, reason)
- 벡터 인덱스 freshness / backlog
- automation suggestion confidence

“안 되면 안 된다고 말하는” 제품 철학이 코드에 박혀 있다.

#### E. 문서·릴리스 게이트 문화

`check_current_release_docs`, OpenAPI drift, i18n literals, bundle budget, SBOM/audit workflows —  
제품 약속이 문서와 코드에서 어긋나기 어렵게 설계되어 있다.

### 2.2 구조적 약점 / 기술 부채

| 영역 | 현상 | 리스크 |
| --- | --- | --- |
| Intent 라우팅 이중화 | `is_file_action_request` + `detect_document_intent` + agent 라우트 + 일반 chat | 경계 케이스에서 “코드만 주고 파일은 없음” 재발 |
| 에이전트 `write_file` 콘텐츠 품질 | Executor 프롬프트에 규칙만 있고 **file_generation 파이프라인을 거치지 않음** | 약한 모델이 agent 경로로 가면 깨진 HTML/fence 저장 가능 |
| Proposal 범위 | binary/docx 등 overwrite fail-closed, stage 불가 | 문서 재생성 UX가 “막힘”으로 느껴질 수 있음 |
| Graph mixin 규모 | retrieval ~1200 LOC, ingest ~700, ingestion ~1100 | 신규 기여자 온보딩 비용, 회귀 위험 |
| 개념 추출 휴리스틱 | `_extract_concepts` / triples 규칙 기반 | 대형 코퍼스에서 노이즈 노드 증가 |
| 웹 수집 경계 | pipeline은 네트워크 안 함; 추출은 upstream | 확장/툴별 품질 편차, 사용자에게 “왜 비었지?” 설명 필요 |
| 멀티파일 프로젝트 | `create_web_project` 존재하나 direct path는 **단일 파일** | “랜딩 페이지 앱 만들어줘” 체감 약함 |
| 승인 루프 | plan 단계 human_in_loop 메시지, UI 완전 대화형 승인은 아직 약함 | 복잡한 agent 작업 중단 시 복구 경로 불명확 |

### 2.3 모듈별 세부 평가

#### `latticeai/core/agent.py` (~844 LOC)

**잘된 점**

- PLAN → APPROVE → EXECUTE → VERIFY → (ROLLBACK) → MEMORY 명확
- `extract_action_details` 관용 파서 + LoopTrace 연동
- scoped knowledge tools의 workspace/user는 **서버가 덮어씀** (모델 소유 금지)
- DONE은 evidence + critic PASS 요구 (9.9.0)

**개선 여지**

1. **Artifact-aware execute:** `write_file` / `create_*` 직후 자동으로 `file_generation.validate` 통과 여부를 transcript에 남기고, 실패 시 executor에 재시도 힌트.
2. **Phase budgets 분리:** plan/execute/verify 토큰·스텝 예산을 역할별로 분리해 약한 모델이 plan에 토큰을 다 쓰지 않게.
3. **Streaming tool progress:** UI에 단계별 “읽기 → 쓰기 → 검증” 타임라인 (현재 transcript는 API에 있으나 홈 UI 노출이 약함).
4. **NEEDS_REVIEW UX 계약:** 프론트가 DONE과 시각적으로 완전히 다르게 렌더하는지 회귀 테스트 고정.

#### `latticeai/core/file_generation.py` (~451 LOC)

**잘된 점:** 순수 함수, 확장자 전략, repair 최후 수단, 메타데이터(`generation.repaired`).

**개선 여지 (파일 생성 P0 후보):**

| 항목 | 현재 | 제안 |
| --- | --- | --- |
| 적용 경로 | 주로 `direct_file_action` | **모든** `write_file` dispatch 직전 post-process 훅 |
| 확장자 | html/json/css/py/js 등 | `.tsx`/`.vue`/`.svelte`, multi-file manifest |
| 검증 | 구조적 | HTML: 필수 태그 + 선택적 light tidy; JS: 괄호 균형; Python: `ast.parse` |
| 멀티파일 | 없음 | `FileManifest` JSON 계획 → 파일별 generate → 묶음 쓰기 |
| 사용자 피드백 | “보정됨” 한 줄 | UI 배지: Valid / Repaired / Fallback scaffold + 미리보기 |
| 벤치 | unit 위주 | **모델 매트릭스 하네스** (gemma/qwen/llama × html/py/json) CI 주간 |

#### `latticeai/api/chat_helpers.py` + `chat_intents.py`

Intent gate는 잘 설계되어 있으나:

- “방법” / “how to” 제외는 좋음
- 하지만 “이 코드 파일로 저장해줘” (채팅 위 코드 블록 참조)는 **대화 컨텍스트 기반 저장**이 약함
- “accounting_app 폴더로 만들어줘” → 프로젝트 스캐폴드 라우트 분리 필요

#### `lattice_brain/ingestion.py` + graph

단일 관문 원칙이 명확. 다음 강화 포인트는 §5.

#### Frontend Brain surface

- Living Brain, depth emergence, FirstFiveCard, Command palette, Review cards — 제품 언어 일관
- 코드 블록 Save, created_files 칩 — 파일 루프의 **UI 씨앗**은 있음
- 부족: 생성 파일 **인라인 프리뷰** (HTML iframe / PDF), “Brain에 넣기” 원클릭, 생성 직후 graph pulse 연동 강도

---

## 3. “사용자에게 확 와닿는” 제품으로

### 3.1 와닿음의 정의 (측정 가능한 성공)

Lattice를 “멋진 로컬 AI”가 아니라 **내 지식이 자라는 실체**로 느끼게 하려면, 신규 사용자가 다음 **네 번의 도파민 순간**을 첫 세션에 겪어야 한다.

| # | 순간 | 현재 | 목표 체감 |
| --- | --- | --- | --- |
| 1 | **첫 저장** | 파일/노트 추가 가능 | 드래그 앤 드롭 직후 Brain이 맥박 + “기억함: N개 조각” |
| 2 | **첫 회상** | RAG + context_quality | 질문에 답이 나오고 **출처 카드가 답 바로 아래**에 붙음 (클릭 시 원문) |
| 3 | **첫 산출물** | 파일 생성 / 문서 생성 | “코드 블록”이 아니라 **다운로드·미리보기·폴더 열기**가 있는 파일 카드 |
| 4 | **첫 보호** | Review Center | “AI가 파일을 고치려 함” → 한 문장 요약 + 승인 한 번 = 안전 자동화 신뢰 |

First 5 minutes 카드(9.9.1)는 이 네 순간의 **체크리스트 골격**이다.  
다음 단계는 카드 완료 시 **시각·사운드·Brain motion**이 보상으로 작동하는 것.

### 3.2 포지셔닝 한 문장 (유지)

> **모델은 목소리다. Brain은 자산이다.**  
> 파일·폴더·웹·대화가 로컬 그래프에 쌓이고, 어떤 모델이든 그 기억으로 일한다.

경쟁 대비 차별화 (유지·강조):

- AnythingLLM / LM Studio: 모델 런너 → Lattice는 **기억 소유권**
- Obsidian: 수동 그래프 → Lattice는 **대화로 자라고 제안으로 보호**
- ChatGPT 프로젝트: 클라우드 락인 → Lattice는 **`.latticebrain` 휴대**

### 3.3 “와닿음” 개선 로드맵 (제품)

#### P0 — 30초 가치 루프 (First Value Loop)

**시나리오 고정 스크립트 (온보딩 기본 트랙):**

1. Wake Brain (이미 존재)
2. “샘플 노트 넣기” 원클릭 (내장 demo corpus 3개 문서)
3. 미리 채워진 질문: “이 노트에서 핵심 결정은?”
4. 답 + **출처 하이라이트** + “Brain이 방금 배운 것” 3 토픽 칩
5. “HTML 명함 페이지 만들어줘” → **파일 카드 + Open / Reveal in Finder**

구현 포인트:

- `FirstFiveCard` 스텝을 **실제 API 성공 이벤트**에만 complete (이미 일부 존재)
- demo corpus는 `lattice_brain` 번들 fixture로 오프라인
- 회상 실패 시 limited reason을 **다음 행동 CTA**로 연결 (“폴더 연결하기”)

#### P1 — 결과물 중심 메시지 UI (Artifact-first chat)

현재: assistant text 중심 + optional files 배열.

목표 메시지 모델:

```text
Message {
  text?: string
  artifacts: Artifact[]   // file | proposal | ingest_summary | proof
  contextQuality?
  loopSummary?            // 접힌 “어떻게 했는지”
}
```

UI:

- 파일: 아이콘 + 이름 + bytes + Valid/Repaired 배지 + Preview + Download + “Brain에 인덱싱”
- HTML: 안전한 sandbox iframe 미리보기
- Proposal: Review 카드 인라인 축약
- 코드만 온 경우: 상단 배너 “파일로 저장되지 않음 — 저장할까요?” + 원클릭 (MessageMarkdown Save 강화)

#### P1 — 지식 성장의 가시성

Capture / folder job 완료 후:

- “+12 documents, +48 chunks, vector 87% fresh”
- 그래프 depth 자동 한 단계 펄 (DepthEmergence와 연동)
- Daily briefing에 “어제 새로 기억한 것” 섹션 (이미 briefing 골격 있음)

#### P2 — 감정 디자인 (과하지 않게)

- Brain idle: 호흡
- ingesting: 유입 파티클
- recalling: 링 발광
- artifact created: 짧은 성공 펄스 (prefers-reduced-motion 존중)
- NEEDS_REVIEW / FAILED: 따뜻한 경고색 (성공 초록과 절대 혼동 금지)

---

## 4. UX Polish — “완벽”에 가깝게

### 4.1 여정별 진단 (Nielsen + 제품 여정)

| 여정 | 상태 | Polish 갭 |
| --- | --- | --- |
| Onboarding / device analysis | 9.9.0 정직화 | 분석 애니메이션 vs 실제 probe 시간 정렬; “모델 없이 계속”을 1순위 CTA로 |
| First 5 minutes | 9.9.1 | 완료 보상이 약함; 스텝 실패 시 복구 카피 |
| Brain home empty | 개선됨 | Capture 진입이 여전히 “다른 페이지” 느낌 |
| Chat streaming | 동작 | agent 단계 스트리밍 UI 부족 |
| File create | direct path 강함 | 미리보기·Reveal·재생성·버전 |
| Review Center | 인간 언어화 | 일괄 approve 안전장치, conflict 409 시 “rebase” UX |
| Capture folder | jobs API | 백그라운드 진행률 토스트/전역 인디케이터 |
| Admin | 분리됨 | 일반 사용자 경로에 admin 개념 누수 없는지 점검 |
| Errors | ko/en 로컬 | 액션 버튼(재시도/모델 로드/권한) 표준화 |
| a11y | 부분 | focus trap, graph 키보드, reduced-motion 전면 감사 |
| Performance | bundle 예산 | 그래프/라이브러리 lazy route 유지·강화 |

### 4.2 UX Polish 체크리스트 (실행 단위)

#### 마이크로카피

- [ ] 모든 terminal agent 상태 문구: DONE / NEEDS_REVIEW / FAILED 구분 고정 i18n 키
- [ ] “파일을 만들었습니다” → “`generated_page.html` (2.4 KB) · 미리보기 · 폴더에서 보기”
- [ ] repaired 시: “모델 출력을 자동 보정했습니다. 내용을 확인해 주세요.” + Preview 강제 유도
- [ ] context limited 시: 이유가 CTA (Capture / 폴더 / 웹)

#### 인터랙션

- [ ] 생성 파일 카드: Preview modal (html/md/txt/json), binary는 다운로드만
- [ ] 드래그앤드롭을 Brain home 전역 hit-target으로 (Capture 전용 페이지 의존 감소)
- [ ] Cmd+K: “최근 생성 파일”, “대기 중 제안”, “인덱싱 중인 폴더” 동적 항목
- [ ] 긴 agent 작업: 하단 sticky progress (step i/n, 현재 tool)

#### 시각

- [ ] 파일 카드 / proposal 카드 / proof 카드 공통 elevation·radius 토큰 통일 (`STYLE_SYSTEM.md` 정렬)
- [ ] 성공/경고/위험 색 의미 고정 (Review risk와 동일 팔레트)
- [ ] empty states: 일러스트 + 한 줄 가치 + 한 버튼 (장문 설명 금지)

#### 신뢰

- [ ] 시뮬레이션/LLM-free 경로는 배지 “시뮬레이션 · 모델 미사용”
- [ ] 네트워크/클라우드 호출 직전 항상 명시 (이미 consent 문화 — UI에서도 동일)
- [ ] 인덱싱 중 질문에 “아직 배우는 중” 배너

### 4.3 측정 (UX가 완벽해졌는지 아는 법)

정성 인터뷰 없이 내부에서라도:

| 메트릭 | 수집 | 목표 (초기) |
| --- | --- | --- |
| TTFV (Time to first value) | firstFive complete timestamp | < 3 min |
| File request → real file rate | `action_route=direct_write_file` + created_files | > 95% |
| Code-only responses on file intent | intent hit but no created_files | < 2% |
| Review approve without conflict | proposal metrics | conflict 시 100% 409+메시지 |
| Retrieval limited rate on grounded Q | context_quality | 샘플 corpus에서 감소 추세 |
| Agent NEEDS_REVIEW rate | loop summary | 추적 (높으면 critic/모델 문제) |

`scripts/product_readiness.py` / brain quality eval에 **제품 퍼널 시나리오**를 추가하는 것을 권장.

---

## 5. 하네스 엔지니어링 관점

### 5.1 현재 하네스 맵

```text
                    ┌─────────────────────────────┐
                    │  CI / release gates         │
                    │  lint, unit, visual, docs   │
                    │  agent_eval, audits         │
                    └─────────────┬───────────────┘
                                  │
     ┌────────────────────────────┼────────────────────────────┐
     │                            │                            │
     ▼                            ▼                            ▼
┌─────────────┐          ┌───────────────┐          ┌──────────────────┐
│ Agent harness│          │ Brain harness │          │ Product harness  │
│ scripted LLM │          │ ingest fixtures│          │ Playwright visual│
│ LoopTrace    │          │ retrieval bench│          │ First-run e2e    │
│ tool fakes   │          │ quality scores │          │ bundle budget    │
└─────────────┘          └───────────────┘          └──────────────────┘
```

**강점:** 에이전트 루프가 “프롬프트 감”이 아니라 **시나리오 게이트**로 보호된다.  
**약점:** 파일 생성·RAG 품질·멀티모델 실제 추론은 결정론 하네스 밖에 있거나 주간/수동에 가깝다.

### 5.2 하네스 엔지니어링 개선안

#### H1. Artifact Generation Harness (최우선)

목표: **어떤 모델이든** “HTML/파일 만들어줘” → 디스크에 유효 파일.

시나리오 매트릭스:

| ID | 사용자 발화 | 기대 path | 검증 |
| --- | --- | --- | --- |
| FG-01 | `hello.html 만들어줘` | `hello.html` | DOCTYPE, html, /html |
| FG-02 | `html 파일 만들어줘` | `generated_page.html` | infer + valid |
| FG-03 | weak model fence+chat noise | (mock generate) | extract strips noise |
| FG-04 | truncated html | repair closes tags | validate true |
| FG-05 | `data.json 만들어줘` | parse JSON | |
| FG-06 | agent path write_file with fences | post-hook clean | no ``` in file |
| FG-07 | “방법만 알려줘 html” | **no file** | stays chat |
| FG-08 | multi-file “todo 앱 html+css+js” | 3 files or project | all valid |

구현:

- 기존 `tests/unit/test_file_generation.py` 확장
- **Scripted model**이 일부러 더러운 출력을 내는 케이스 (agent_eval 스타일)
- 주간 job: 실제 로컬 모델 스모크 (`scripts/bench_models.py` 연동) — fail-open 리포트, CI 필수 아님

#### H2. Loop Contract Harness 강화

이미 있는 시나리오에 추가:

- `write_file` 후 critic이 파일 존재/내용을 근거로 PASS
- proposal path: mutation → transcript `decision=proposed` → DONE이 아닌 적절한 final_message
- parse_error ×3 → terminal non-success
- tool result에 `created_files` 누락 시 UI 계약 테스트 (frontend)

#### H3. Knowledge Pipeline Harness

E2E 결정론:

1. temp dir에 샘플 폴더 생성
2. `ingest_folder` → job complete
3. hybrid search query → nodes ≥ N
4. chat with graph context → `context_quality.mode != none`
5. automation suggestions confidence 필드 존재

폴더 대용량: latency budget (기존 scale diagnostics 활용).

#### H4. Regression Golden Files

`tests/fixtures/filegen/` 에 dirty model outputs → expected cleaned files.  
프롬프트 변경 시 골든 업데이트는 리뷰 필수.

#### H5. Harness 설계 원칙 (유지)

1. **모델은 untrusted** — 파서·검증·거버넌스가 진실
2. **결정론 우선** — CI는 scripted; live model은 schedule
3. **실패 모드 이름 붙이기** — LoopTrace repair tags, generation.repaired
4. **제품 계약 테스트** — API 필드 `created_files`, `context_quality`, `final_state`
5. **Fail-closed over fail-open** — 검증 불명 = NEEDS_REVIEW, 덮어쓰기 불가 = 409

---

## 6. 루프 엔지니어링 관점

### 6.1 현재 루프 구조

```text
User message
    │
    ├─ intent: clear / network / no model → short-circuit
    ├─ intent: file action → direct_file_action
    │         (generate_file_content → write_file → created_files)
    ├─ intent: document → document_generator (+ graph context)
    └─ else → chat RAG answer  OR  agent loop
                    │
                    ▼
         PLAN → APPROVE → EXECUTE* → VERIFY → MEMORY
                    │              │
                    │              └─ critic PASS+evidence → DONE
                    │              └─ unparseable / no evidence → NEEDS_REVIEW
                    └─ high-risk without human → FAILED
```

### 6.2 루프의 강점

- **역할 분리** (planner / executor / critic / memory) — 단일 프롬프트 God-agent 대비 디버깅 가능
- **관측** LoopTrace (llm_call, parse_error, repair, tool outcome, decision)
- **교정 채널** corrections → executor context
- **루프 가드** 동일 action+args 반복 중단
- **스코프 강제** knowledge tools

### 6.3 루프 취약점과 개선

#### L1. 파일 생성 이원화 (가장 큰 실무 갭)

| 경로 | 품질 파이프라인 | 약한 모델 |
| --- | --- | --- |
| direct_file_action | ✅ generate_file_content | 강함 |
| agent execute `write_file` | ❌ raw args.content | 약함 (fence/chat) |
| 일반 chat (intent 미탐지) | ❌ 코드 블록만 | 사용자가 수동 Save |

**개선 (권장 아키텍처):**

```text
                    ┌────────────────────────┐
   any write_file ─►│ ArtifactWritePipeline  │
                    │ 1. extract_file_content│
                    │ 2. validate            │
                    │ 3. optional re-gen     │
                    │ 4. repair              │
                    │ 5. governor/proposal   │
                    │ 6. disk write          │
                    │ 7. optional re-ingest  │
                    └────────────────────────┘
```

- `tool_dispatch` 또는 `write_file` 래퍼에서 **단일 진입**
- agent / chat / telegram / API tools 전부 동일 보장
- “모델이 JSON args에 코드를 넣든, chat noise를 넣든” 디스크 결과는 유효

#### L2. Artifact Loop (신규 루프 모드)

단순 Q&A와 구분되는 **산출물 루프**:

```text
INTENT(artifact)
  → PLAN_MANIFEST (files[], acceptance[])
  → FOR each file: GENERATE → VALIDATE → WRITE
  → VERIFY_BUNDLE (links, imports, open html)
  → PRESENT_CARDS
  → INGEST_ARTIFACTS (optional)
```

- 단일 HTML: manifest 1파일
- “랜딩 페이지”: html+css+js 또는 인라인 html 정책 명시
- 수락 기준을 critic이 검사 (`</html>` 존재, JSON parse, 등 **결정론 체크 우선**, LLM critic 보조)

약한 모델일수록 **LLM critic 비중↓, 결정론 verifier 비중↑**.

#### L3. Plan 품질 루프

현재 plan 파싱 실패 시 empty steps fallback — 실행이 표류할 수 있음.

개선:

- plan 최소 스키마 검증 (goal, steps[1..])
- steps 비면 **heuristic plan** (file intent → write_file 1 step)
- estimated_steps와 max_steps 정합

#### L4. Human-in-the-loop 실제화

`WAITING_APPROVAL`이 human 없이 FAILED로 떨어지는 경로는 안전하지만 UX가 끊긴다.

개선:

- API: `status=awaiting_approval` + plan steps 요약
- UI: 인라인 “승인하고 실행” / “수정해서 실행”
- 승인 토큰 짧은 TTL

#### L5. Memory 루프 품질

Memory updater가 Experience로 들어가도, **검색에 쓸모 있는 학습**인지 품질 필터 필요.

- 중복 learning 병합
- “파일을 만들었다” 수준 trivial 학습 저장 억제
- 사용자 visible “Brain이 배운 것” 토글

#### L6. 멀티에이전트 / 워크플로 루프

`multi_agent` / workflow_engine / automation install 존재.  
체감 강화:

- 설치된 automation의 **마지막 실행 결과**를 Act + Briefing에
- 실패 시 Review queue로 (이미 draft 게이트 — 실행 로그 연결)

### 6.4 루프 엔지니어링 원칙 (권장 문서화)

1. **모든 side effect는 도구를 통한다** (채팅 텍스트로 파일 “인 척” 금지)
2. **모든 도구 결과는 transcript의 진실** (환각 final 금지 — 이미 방향 일치)
3. **검증은 가능하면 결정론** (파일 존재, 스키마, 해시, 테스트 명령)
4. **약한 모델 = 더 짧은 루프 + 더 강한 하네스** (direct path, repair, 낮은 temperature)
5. **관측 없이 최적화 금지** (LoopTrace / generation meta / context_quality)

---

## 7. File · Folder · Web → Graph → RAG → Automation 강화

### 7.1 현재 파이프라인 (정직함은 이미 있음)

```text
Source
  file upload / local path
  folder walk (.latticeignore)
  web_url / browser_tab (extracted text only)
  chat_message / mcp_message
  decision / experience
        │
        ▼
 IngestionPipeline.ingest
  normalize → hash → quality score → graph ingest → provenance
        │
        ▼
 KnowledgeGraphStore
  nodes/edges/chunks + optional incremental vector index
        │
        ▼
 Retrieval (lexical + vector hybrid) → context_quality
        │
        ▼
 Chat / Document gen / Agent / Automation suggestions
```

### 7.2 단계별 강화 설계

#### A. 파일 인덱싱

| 개선 | 설명 | 우선순위 |
| --- | --- | --- |
| 생성 파일 자동 재수집 | `write_file` 성공 시 workspace 파일이면 `IngestionPipeline` 옵션 호출 | P0 |
| MIME/인코딩 견고화 | 한글 경로, UTF-16, 이진 오인 방지 | P1 |
| 파서 플러그인 | pdf/docx/xlsx 추출 품질 점수 연동 강화 | P1 |
| 중복·버전 | 동일 sha256 skip + “업데이트됨” 엣지 | P1 |
| 코드 심볼 인덱스 | 함수/클래스 노드 (점진) | P2 |

#### B. 폴더 인덱싱

이미: background jobs, latticeignore, skip dirs, size limits.

강화:

1. **Watch mode (옵트인)** — FSEvents/watchdog로 증분 (동의 UI 필수)
2. **완료 리포트 카드** — 추가/스킵/실패/저품질 샘플 3개
3. **워크스페이스 스코프** 명확 배지 (personal vs org)
4. **우선순위 큐** — 사용자가 연 파일/최근 수정 먼저 인덱싱
5. ** thrash 방지** — node_modules 등 기본 스킵 유지 + 사용자 보이기

#### C. 웹 인덱싱

경계: pipeline은 fetch 안 함 (보안·결정론). 올바른 방향.

강화:

1. 브라우저 확장 추출 품질을 **파이프라인 quality와 동일 스키마**로 표시
2. “추출 빈약” 시 사용자에게 **원문 하이라이트 / 재캡처 / 수동 붙여넣기**
3. 동일 URL 재방문 시 diff 요약 노드 (“무엇이 바뀌었나”)
4. 읽기 모드 실패 시 raw HTML 저장 여부 명시 (용량·프라이버시)
5. MCP/tool fetch 경로도 **반드시** `ingest_web_page` 동일 문

#### D. 그래프 강화

| 주제 | 현재 | 다음 |
| --- | --- | --- |
| 스키마 | 풍부 (Document, Concept, Source, …) | 사용 빈도 낮은 타입 정리 가이드 |
| 엣지 동사 | 한국어 관계 라벨 | 정규화 사전 (작성함/포함함/관련) + 영문 매핑 |
| 노이즈 | 휴리스틱 concept | IDF/빈도 컷, curator job (graph_curator 확장) |
| 출처 | provenance 있음 | UI proof와 100% 필드 정렬 |
| 시간 | temporal 테스트 존재 | “지난주 결정” 쿼리 UX |
| 충돌 | MemoryQualityManager | 사용자 resolve UI |

#### E. RAG 강화

현재 hybrid + honesty signal은 차별점.

다음 품질 레버:

1. **쿼리 클래스별 fusion** — 사실 회상 / 코드 / 사람 / 최근 이벤트 가중치 다름 (`ROADMAP` track 1)
2. **청킹 전략** — 코드는 AST/심볼, 문서는 헤딩, 채팅은 turn
3. **리랭크(로컬)** — 교차 인코더 없이도 lexical feature rerank
4. **인용 강제** — 답변에 source id 없으면 UI가 “근거 없음” 표시 (환각 억제)
5. **네거티브 캐시** — 빈 결과 반복 질문 시 Capture CTA
6. **벤치 고정** — `retrieval_benchmark_fixtures` + CI 임계값 (회귀 시 fail)

#### F. 자동화 강화

현재: 패턴 마이닝, suggestion confidence, install → disabled draft + review.

체감 강화:

1. **설치 후 “지금 한 번 실행 (dry-run)”** 버튼
2. 실행 로그를 Brain timeline / briefing에
3. 폴더 digest automation: “새 파일 3개 요약 준비됨” 푸시성 배너 (로컬 알림 옵트인)
4. 질문 클러스터 제안: 예시 질문 클릭 → 즉시 chat prefill
5. 자동화 실패 = Review/Care 패널 한곳에 모음

### 7.3 “훨씬 더 강하게”의 목표 아키텍처

```text
┌──────────── Capture Fabric ────────────┐
│ Drop / Folder watch / Extension / Chat │
└─────────────────┬──────────────────────┘
                  ▼
┌──────────── Normalize & Quality ───────┐
│ hash · mime · extract · quality score  │
└─────────────────┬──────────────────────┘
                  ▼
┌──────────── Graph Write Master ────────┐
│ idempotent upsert · provenance · scope │
└─────────────────┬──────────────────────┘
                  ▼
┌──── Index Fabric (async) ──────────────┐
│ chunks · FTS · vectors · symbol index  │
│ freshness API · backlog reasons        │
└─────────────────┬──────────────────────┘
                  ▼
┌──── Reason Fabric ─────────────────────┐
│ hybrid retrieve · cite · agent tools   │
│ artifact pipeline · proposals          │
└─────────────────┬──────────────────────┘
                  ▼
┌──── Act Fabric ────────────────────────┐
│ automations · briefing · review queue  │
└────────────────────────────────────────┘
```

핵심: **Capture → Index freshness → Cite → Act** 가 사용자에게 끊기지 않는 한 줄 스토리.

---

## 8. 모델 불문 “코드가 아니라 파일로” 완벽 수신

### 8.1 문제 정의

사용자가 기대하는 것:

> “랜딩 페이지 HTML 만들어줘” → **디스크에 열리는 파일** + UI에서 받기

모델이 하는 것 (특히 소형 로컬):

- 설명 + 마크다운 fence
- 불완전 `</html>`
- JSON tool 스키마 실패
- “여기 코드입니다”만 채팅에 출력

### 8.2 이미 있는 방어선

1. Intent gate (`is_file_action_request`)
2. Path infer (`file_action_target` / `infer_file_target`)
3. `generate_file_content` pipeline
4. `created_files` API 계약 + 프론트 칩
5. 코드 블록 **Save as file** 폴백

### 8.3 남은 실패 모드

| # | 실패 모드 | 원인 | 대응 |
| --- | --- | --- | --- |
| 1 | Intent 미탐지 | 애매한 한국어 (“페이지 좀”) | 의도 분류기 확장 + 확인 칩 “파일로 만들까요?” |
| 2 | Agent 경로 raw write | file_generation 미적용 | **전역 ArtifactWritePipeline** |
| 3 | 멀티파일 | 단일 path 가정 | Manifest 루프 |
| 4 | 과 aggresive intent | how-to 제외는 있으나 예외 | 골든 테스트 FG-07 |
| 5 | 문서(docx) vs 텍스트 | document_generator vs write_file | 라우팅 표 문서화 + 테스트 |
| 6 | UI가 파일을 안 보여줌 | stream 경로 meta 누락 | SSE trailer 계약 테스트 |
| 7 | 사용자가 경로를 모름 | agent_workspace 숨김 | Reveal in Finder / 라이브러리 탭 자동 포커스 |
| 8 | 재생성 overwrite | governor fail-closed | “generated_page_2.html” 자동 접미사 또는 proposal |
| 9 | 거대 파일 토큰 부족 | max_tokens | 청크 생성 후 assemble (html sections) |
| 10 | 모델 거부 | refusal detect | repair placeholder + “다른 모델로 재시도” CTA |

### 8.4 목표 UX 계약 (제품 스펙)

**입력:** 파일 생성 의도 탐지  
**출력 (항상 하나):**

```json
{
  "final_state": "DONE",
  "response": "generated_page.html 파일을 만들었습니다.",
  "created_files": [
    {"path": "...", "filename": "generated_page.html", "bytes": 1234, "action": "write_file"}
  ],
  "generation": {"attempts": [...], "repaired": false},
  "artifacts": [
    {"kind": "file", "previewable": true, "valid": true}
  ]
}
```

**금지:**

- 파일 의도인데 `created_files` 없이 fence만 있는 200 OK를 “성공”으로 포장
- repaired scaffold를 고품질 성공처럼 과장 (정직 배지 필수)

### 8.5 구현 청사진 (우선순위)

#### Phase A — 단일 진입 쓰기 (1 PR 크기)

1. `ArtifactWritePipeline.write(path, raw_content, user_request, source)`  
2. `tools/filesystem.write_file` 또는 dispatch 레이어에서 호출  
3. agent / direct / API 동일  
4. unit: dirty content → clean file

#### Phase B — Intent + UI 완결

1. 애매 의도 시 confirm chip  
2. 메시지 Artifact card (preview/download/reveal/ingest)  
3. stream 경로 `created_files` 보장 테스트  
4. “코드만 옴” 탐지 → 저장 CTA

#### Phase C — Multi-file / Project

1. `infer_project_manifest(message)`  
2. `create_web_project`와 통합 또는 manifest 실행  
3. acceptance: index.html loads, no broken local refs  
4. 결과에 zip 다운로드 옵션

#### Phase D — 모델 매트릭스

1. scripted dirty outputs (CI)  
2. optional live models job  
3. 대시보드: model × filetype success rate

### 8.6 프롬프트 / 디코딩 팁 (루프 쪽)

- 파일 생성 temperature ≤ 0.3 (이미 direct path 적용)
- `max_tokens` 하한 4096 (이미)
- stop sequences: 불필요 설명 유도 감소
- 시스템: “Your entire reply is saved verbatim” (이미) — agent JSON 경로에는 **content 필드 전용 재생성**이 더 안전 (한 번에 JSON+긴 HTML을 넣지 말 것)

**권장 패턴 (약한 모델):**

```text
Step1: action=write_file args={path only}  → server asks model for content via file_generation
```

즉 모델이 JSON 안에 장문 HTML을 넣게 하지 말고, **path 선언과 content 생성을 분리**.

---

## 9. 우선순위 로드맵 (실행 순서)

### Wave 0 — 계약 고정 (3–5일)

- [ ] ArtifactWritePipeline 설계 + write_file 단일화
- [ ] 파일 의도인데 created_files 없는 경우 제품 지표/테스트
- [ ] DONE vs NEEDS_REVIEW 프론트 시각 회귀 고정
- [ ] 본 문서 권장 메트릭을 product_readiness에 1–2개 연결

### Wave 1 — 와닿음 (1–2주)

- [ ] First Value Loop (샘플 노트 → 회상 → HTML 파일)
- [ ] Artifact message cards (preview/download/reveal)
- [ ] 생성 파일 자동 Brain ingest 옵션
- [ ] folder job 완료 리포트 카드

### Wave 2 — 파이프라인 강도 (2–4주)

- [ ] 쿼리 클래스별 retrieval fusion + 벤치 임계값
- [ ] 웹 추출 품질 CTA / 재캡처
- [ ] graph curator 노이즈 감소 job
- [ ] automation dry-run + 실행 로그 표면화

### Wave 3 — 루프/하네스 성숙 (병행)

- [ ] Artifact Loop mode (manifest)
- [ ] human approval UI for WAITING_APPROVAL
- [ ] agent_eval + filegen harness 통합 게이트
- [ ] 주간 multi-model filegen 리포트

### Wave 4 — Polish perfection

- [ ] a11y 전면, reduced-motion
- [ ] 전역 DnD capture
- [ ] conflict rebase UX for proposals
- [ ] multi-file project zip + preview

---

## 10. 권장 다음 리팩터 / 기능 (AGENTS.md 정렬)

AGENTS.md Preferred Refactoring Order와 맞추면:

1. **(제품) ArtifactWritePipeline** — ToolRegistry/dispatch 경계에 자연스럽게 안착  
2. **(Brain) Retrieval fusion + bench gates** — KG stabilization  
3. **(Runtime) Approval UI + Artifact Loop** — AgentRuntime 강화  
4. **(Docs) 파일 생성 제품 계약** — README “What You Can Do”에 파일 카드 스크린샷  
5. **(UI) Artifact-first chat** — feature enhancements

---

## 11. 리스크와 비목표

### 리스크

- Intent를 넓히면 how-to 질문까지 파일 생성 → **좁은 게이트 + confirm chip** 유지
- 자동 re-ingest가 대규모 쓰기를 유발 → debounce / 사용자 토글
- HTML iframe preview XSS → sandbox + CSP
- 폴더 watch 프라이버시 → 명시 동의, 기본 off
- 결정론 repair가 “엉뚱한 페이지”를 성공처럼 보여줌 → repaired 배지 강제

### 비목표 (지금 하지 말 것)

- 클라우드 기본화
- 그래프를 홈의 주 UI로 복귀 (Brain-first 유지)
- 검증 없는 완전 자율 삭제/덮어쓰기
- 모든 파일 타입 OCR/멀티모달 한 방에 (점진)

---

## 12. 파일·폴더 참조 인덱스 (리뷰 근거)

| 주제 | 경로 |
| --- | --- |
| Agent loop | `latticeai/core/agent.py` |
| Loop prompts | `latticeai/core/agent_prompts.py` |
| Loop trace | `latticeai/core/agent_trace.py` |
| Agent eval | `latticeai/core/agent_eval.py`, `scripts/agent_eval.py` |
| File generation | `latticeai/core/file_generation.py` |
| Tool governor | `latticeai/core/tool_governor.py` |
| Tool registry | `latticeai/core/tool_registry.py` |
| Chat intent / direct write | `latticeai/api/chat_intents.py`, `chat_helpers.py`, `chat.py` |
| Tool dispatch / created_files | `latticeai/services/tool_dispatch.py` |
| Change proposals | `latticeai/services/change_proposals.py` |
| Documents tools | `latticeai/tools/filesystem.py`, `documents.py` |
| Ingestion | `lattice_brain/ingestion.py` |
| Graph ingest/retrieval/vector | `lattice_brain/graph/ingest.py`, `retrieval.py`, `retrieval_vector.py` |
| Document generation | `latticeai/core/document_generator.py` |
| Brain chat UX | `frontend/src/features/brain/hooks/useBrainChat.ts` |
| Message / save code | `frontend/src/features/brain/MessageMarkdown.tsx` |
| First 5 minutes | `frontend/src/features/brain/FirstFiveCard.tsx` |
| Review UI | `frontend/src/features/review/*` |
| Prior reviews | `docs/reviews/*`, `docs/USABILITY_AUDIT.md`, `docs/PRODUCT_DIRECTION_REVIEW.md` |

---

## 13. 최종 권고 (경영 요약)

LatticeAI 9.9.1은 **신뢰 가능한 로컬 Brain**의 기초 공사를 끝낸 상태다.  
사용자가 “와” 하는 순간은 더 이상 아키텍처 다이어그램이 아니라:

1. **넣은 것이 바로 기억되고**  
2. **물어보면 출처와 함께 돌아오고**  
3. **만들라고 하면 파일이 손에 쥐어지고**  
4. **고치려 하면 안전하게 물어보는**

이 네 문장이다.

기술적으로 가장 레버리지가 큰 한 방은:

> **모든 파일 쓰기를 `file_generation` 검증 파이프라인으로 단일화하고,  
> 채팅 UI를 Artifact-first로 바꾸며,  
> 그 계약을 agent_eval·product harness로 영구 고정하는 것.**

그 위에 폴더 워치·retrieval fusion·automation dry-run을 쌓으면,  
“파일·폴더·웹을 인덱싱해 그래프에 RAG하고 자동화한다”는 약속이 **데모가 아니라 매일의 습관**이 된다.

---

## 14. 부록 — 제안 PR 슬라이스 (그래프로 쪼개기)

| PR | 제목 | 검증 |
| --- | --- | --- |
| PR-1 | ArtifactWritePipeline + write_file 단일화 | unit filegen + agent dirty write |
| PR-2 | created_files SSE/UI 계약 + Artifact card | frontend unit + visual |
| PR-3 | post-write optional ingest + library focus | integration ingest |
| PR-4 | First Value Loop demo corpus | product_readiness + e2e |
| PR-5 | retrieval query-class fusion + bench gate | retrieval fixtures CI |
| PR-6 | multi-file manifest artifact loop | agent_eval new scenarios |
| PR-7 | approval awaiting UI | visual + api contract |
| PR-8 | automation dry-run + run log | unit automation |

각 PR은 독립 배포 가능해야 하며, 문서(README 스크린샷/CHANGELOG)는 **체감 변화 있는 PR에만** 동기화한다.

---

*이 문서는 2026-07-21 코드 열람 기준 정적 리뷰이다. 실사용자 인터뷰·외부 펜테스트·전 모델 장기 벤치마크는 포함하지 않았다.*
