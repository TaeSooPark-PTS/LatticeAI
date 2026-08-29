# Lattice AI v11.5.1 — Rust Full Loop (2026-08-12)

> **Status: historical** — point-in-time release note.

v11.5.0이 명시한 잔여 2건을 완결합니다 — **에이전트 루프 오케스트레이션의
Rust 이식(§4c)**과 **문서 생성용 컨텍스트 빌더(§3b 이월분)**. 이로써
목표 아키텍처 다이어그램의 모든 Rust 박스가 구현·증명되었고, Python은
다이어그램에 그려진 그대로의 **AI Worker**(LLM 추론 · 도구 핸들러 실행 ·
그래프 쓰기 단일 작성자)로 남습니다 — 이것이 전환의 완성 형태입니다.
설계: [docs/v11.5.1_RUST_FULL_LOOP_PLAN.md](../v11.5.1_RUST_FULL_LOOP_PLAN.md)

## 에이전트 루프가 Rust로 (lattice-agent)

- **오케스트레이터 전체 이식**: plan→approve→execute→verify→
  (retry/rollback) 상태기계(200 전이 캡), 실행 루프의 전 규칙(파스 예산·
  에스컬레이션·반복 생성 가드·SCOPED 인자 강제·step_index 산식·거버너
  선결정 순서·게이트 3단), 검증 fail-closed 매핑 표, 롤백 3-tier(git→
  스냅샷→none), 승인 스토어(sha256 토큰·TTL 600s·만료 410+replan·원자
  저장). 라우트 `POST /rust/agent/run`(SSE `agent_step`)·`/resume`·
  `/approvals`.
- **증명**: 결정적 헬퍼 그리드(액션 파서 38·플랜 정규화 24·추론 22·
  판정 매핑 90 등) + **실제 Python `SingleAgentRuntime`를 대본 LLM으로
  구동한 end-to-end 궤적 10종이 byte-identical**(최종 상태·상태 이력·
  전체 트랜스크립트·감사 트레일까지). 양방향 계약 pytest 29.
- **실통신 검증**: 실제 워커를 게이트웨이 뒤에 띄워 시임 4종 확인 —
  `/agent/llm` 응답, `/agent/tool` 실제 파일 쓰기, 서킷 브레이커 403,
  기존 파일 수정 제안이 **실제 리뷰 큐 제안으로 생성**. 모델 없는
  워커에서의 전체 턴은 Python과 동일하게 fail-closed
  (`NEEDS_REVIEW`)로 종료 — 성공 조작 없음.

## AI Worker 시임 3종 (Python, additive)

`POST /agent/llm`(부작용 0 완성 호출) · `POST /agent/tool`(도구 핸들러
실행 — **서버측 모드 불변 가드 유지**: 서킷 브레이커/파괴 403,
fail-closed 409; `LATTICEAI_AGENT_TOOL_SEAM=1` 게이트, 기본 꺼짐,
호스트가 자기 워커에만 주입) · `POST /agent/change-proposal`(제안-우선
경로 verbatim). 전부 문·분기 100%, ko/en 로컬라이즈.

## 문서 생성 컨텍스트 네이티브 (lattice-retrieval)

`search_for_document_generation`(2-lane 후보·0.5/0.3/0.2 가중·1.2
부스트·log1p 그래프 점수) · `multi_hop_context`(확장 라운드 hop 라벨) ·
`retrieve_context_for_generation` 전체 계약(self-model 예산 산식·4섹션·
`\n### ` 백오프 트림·sources 체인) — **신규 골든 53(총 247), 전부 완전
일치**, 계약 pytest 256. Python set 순회 비결정성은 양측 공통 정규화로
정직하게 잠금. 라우트 `POST /rust/context/document`.

## 문서

ARCHITECTURE.md **System Map 다이어그램을 Rust 기반 현재 상태로 재작성**
— Lattice Host front-door(6크레이트 + 수퍼바이저)가 중심, Python AI
Worker 박스(시임 3종·단일 작성자), 서킷 브레이커는 Rust 커널과 워커
양쪽에서 집행.

## 검증

Python **7,006 테스트 · 문·분기 100.00%** · 프론트 **1,761 · 100%×4** ·
**Rust 739 테스트** · 골든 5계열(검색·히스토리·컨텍스트/docgen/청킹/
권한·명령/루프 궤적) 전부 양방향 · fresh-resolve 3.11 재검증 ·
실토폴로지 라이브 스모크.

## 산출물

- `dist/ltcai-11.5.1-py3-none-any.whl`
- `dist/ltcai-11.5.1.tar.gz`
- `ltcai-11.5.1.tgz`
- `dist/ltcai-11.5.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.5.1_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다.
