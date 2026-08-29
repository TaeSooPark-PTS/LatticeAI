# Lattice AI v9.9.4 — Durable Loops (2026-07-26)

> **Status: historical** — point-in-time release note.

9.9.4는 2026-07-25 전체 코드 리뷰(`docs/reviews/CODE_REVIEW_2026-07-25_UX_HARNESS_LOOP_KG_RAG.md`)의
개선 로드맵 Wave 0–4를 전부 출하한다. 주제는 리뷰가 제안한 그대로 —
**승인과 실행이 재시작을 이기고, 검색 정책이 하나이며, 사용자가 루프 중간을 보고,
인용이 원문까지 닿는다.**

## Highlights

### Wave 0 — 계약 고정 (Durable / Bounded / Unified)

- **승인/실행 상태 영속화.** `awaiting_approval` 런이 프로세스 메모리가 아니라
  디스크(`data/agent_runs/`, 파일당 1런, 토큰은 SHA-256 해시만 저장)에 미러링된다.
  서버 재시작 후에도 유효 토큰이면 그대로 재개되고(`latticeai/core/run_store.py`),
  만료된 런은 410과 함께 원래 요청 메시지를 담은 **재계획(replan) 힌트**를 돌려준다.
  `GET /agent/approvals`가 사용자별 대기 승인 목록(메모리 ∪ 디스크)을 제공한다.
- **Executor 컨텍스트 슬라이딩 윈도우.** 실행 프롬프트가 전체 transcript를 매 스텝
  임베드하던 O(steps²) 성장 대신, 최근 N스텝(기본 8)만 전체로 싣고 이전 스텝은
  한 줄 요약으로 압축한다. 도구 출력 문자열은 스텝당 캡(기본 700자, 검증자는
  1200자)으로 절단하되 잘린 양을 명시한다. 교정(corrections)은 최신 3개만 반영.
  `LATTICEAI_AGENT_TRANSCRIPT_*` 환경 변수로 조정 가능(`TranscriptBudget`).
- **RetrievalPolicy 단일화.** 새 모듈 `lattice_brain/graph/retrieval_policy.py`가
  질의 재작성(규칙 기반, `LATTICEAI_QUERY_REWRITE=0`으로 차단) + 질의 분류 +
  fusion 가중치 + recency 반감기를 한 곳에서 결정하고, 3채널 서비스 fusion과
  2채널 그래프 fusion이 **같은 정책**을 소비한다(계약 테스트로 고정). 응답에
  `policy {search_query, rewrite_rules}`가 추가로 실린다.
- **Recency age-decay.** recency 클래스 질의는 fused score에 14일 반감기 감쇠를
  `[0.5, 1.0]` 밴드로 곱한다(`scores.age_decay`) — 오래된 문서는 감쇠되되 결코
  0이 되지 않고, fact/code/person 클래스는 바이트 동일하게 유지된다.
- **Plan ← manifest rewrite.** 플래너가 "todo 앱 html+css+js" 류 요청에 빈/부분
  계획을 내놓으면 `normalize_plan`이 direct path와 동일한 결정론적 프로젝트
  매니페스트로 스텝을 재작성한다(`manifest_steps` / `manifest_rewrite` fix로 기록).
  모든 파일 타입을 이미 커버한 계획은 건드리지 않는다.

### Wave 1 — 루프 가시성

- **에이전트 스텝 라이브 스트리밍.** `AgentDeps.on_step` + 런별 `ctx.on_step`
  관측 포트가 plan/approval/execute/verify/rollback/terminal 이벤트를 발행하고,
  스트리밍 채팅은 실행 중에 `event: agent_step` SSE 프레임을 흘려보낸다
  (`agent_live_stream`) — 최종 페이로드 프레임 형태는 기존과 동일(하위 호환).
  UI는 실행 중 스텝 타임라인을 실시간 갱신하고, 완료 후에는 transcript 기반
  타임라인으로 접힌 요약을 보여준다.
- **근거 배지 → 출처 카드 → 원문.** AnswerProof의 인용 출처를 클릭하면 그래프
  노드의 저장 본문/요약을 모달로 보여준다.
- **Watch/잡 건강 신호.** 홈에 폴더 watch 카드(마지막 스캔 시각, ingest/실패
  카운트, 최근 오류 샘플, "실시간 아님 — 주기 스캔" 정직 문구)가 노출된다.
- **표면 패리티 매트릭스.** `docs/SURFACE_PARITY.md`가 Web/Desktop/VS Code/
  Browser/Telegram 표면별 제공 범위를 ✅/◐/—/✖로 기록한다. ✖는 백로그다.

### Wave 2 — Graph RAG 품질 본체

- **타입별 청킹.** 마크다운은 헤딩 경계(+`heading_path` "Guide > Setup" 프로버넌스),
  코드는 함수/공백 라인 경계로 청킹한다. plain 텍스트는 기존과 **바이트 동일**
  경계·동일 chunk id를 유지해 기존 인덱스가 그대로 유효하다. 모든 청크에
  `strategy`/`start_char` 메타가 추가된다(additive, 마이그레이션 없음).
- **PDF 페이지 메타.** PDF 청크는 페이지별 추출 오프셋에서 유도한 1-based
  `page` 번호를 갖는다. 페이지 맵이 본문과 안 맞으면 라벨을 생략한다 —
  틀린 라벨보다 정직한 부재.
- **Embedder fingerprint.** 벡터 인덱스에 임베더 model_id+dim 지문을 기록하고,
  임베더 교체 시 `index_status().embedder.stale_embedder`, vector_freshness
  `stale_embedder` 상태, hybrid 응답 `vector_degraded: "stale_embedder"`로
  3면에서 정직하게 보고한다(이전엔 벡터 채널이 조용히 0건이 됐다).
- **인용 프롬프트 + grounding bench.** 컨텍스트가 있을 때 답변 프롬프트가
  [1]/[2] 스타일 출처 인용을 지시한다(`CITATION_INSTRUCTION`, 4개 프롬프트
  경로를 `_compose_system` 하나로 통일). 합성 grounding 벤치가 CI에서
  판정 정확도를 게이트한다.
- **주기적 noise curate 제안.** 그래프가 커지면(≥200 노드, 마지막 정리 7일
  경과) Command Center 브리핑에 hygiene 섹션 + 원클릭 dry-run quick action이
  나타난다. 적용 시각은 graph_meta `last_noise_curate_at`에 기록.

### Wave 3 — 하네스 확장

- **워크플로/멀티에이전트 5시나리오.** happy path, 실패 노드 정직 종료,
  체크포인트 재개, 권한 거부, 실패 후 상태 일관성 — 전부 결정론 CI 게이트.
- **Funnel soft gate.** `scripts/funnel_soft_gate.py`가 code_only_rate(>5%),
  needs_review_rate(>30%)를 advisory로 경고한다(기본 exit 0, `--strict` 옵트인,
  분모 없으면 "no data"). funnel에 `approval_pauses`/`approval_resumes` 카운터와
  `approval_resume_rate`가 추가됐다.
- **주간 실모델 agent smoke.** `scripts/bench_agent_smoke.py`가 설치된 로컬
  모델로 실제 에이전트 루프 3태스크를 돌려 final_state/steps/repairs/시간을
  보고한다. 모델이 없으면 정직한 skipped 리포트로 exit 0(fail-open).
  주간 GitHub Actions(`agent-smoke.yml`)는 리포트 아티팩트만 남기고 절대
  레포를 게이트하지 않는다.

### Wave 4 — 범위 확장

- **매니페스트 확장.** `react`/`vite` 요청은 Vite+React 5파일 템플릿
  (package.json/index.html/src/main.jsx/src/App.jsx/src/App.css)로, "python
  패키지" 요청은 `__init__.py`/`core.py`/`cli.py`/README 4파일 패키지로
  추론된다. Vite 번들의 index.html은 module-entry 규칙을 쓴다.
- **승인 TTL UX.** 승인 카드에 실시간 카운트다운(2분 미만 앰버 경고), 만료 시
  서버 호출 없이 만료 표시 + 410 replan 힌트 기반 "다시 계획하기" 원클릭.
- **그래프 승격 인간 검토 모드.** `LATTICEAI_GRAPH_PROMOTION_REVIEW=1`(또는
  enterprise capability `GRAPH_PROMOTION_REVIEW`)이면 큐레이터 승격이 즉시
  쓰기 대신 `pending_promotions`로 스테이징되고, `GET /knowledge-graph/promotions`
  + `apply`/`reject` API로 사람이 결정한다.
- **터미널 상태 학습 정책.** FAILED/NEEDS_REVIEW 런도 "무엇이 잘못됐는지"를
  학습으로 남기고, 경험 레코드의 status가 실제 터미널 상태를 반영한다.

### UX (프론트엔드)

- 실행 중 스텝 타임라인, 출처→원문 모달, watch 건강 카드, 승인 카운트다운/재계획,
  생성 파일 "Brain에 기억됨 ✓/대기/실패" 칩, 데모 코퍼스 완료 후 "내 폴더
  연결하기" CTA, 약한 모델 보정 횟수 노트("모델 응답을 N회 보정했어요") —
  모두 ko/en i18n 페리티와 번들 예산 게이트를 통과한다.

## Honest Limitations

- 스텝 스트리밍은 채팅 라우트의 에이전트 경로(stream=true)에서만 라이브다.
  `/agent/resume` 재개 실행은 완료 후 transcript 기반 타임라인으로 표시된다.
- grounding 판정은 여전히 토큰 겹침 휴리스틱(annotation-only)이다 — 요약형
  답변의 편차 케이스는 벤치에 알려진 한계로 문서화되어 있다.
- 인용 지시는 프롬프트 지시일 뿐 강제가 아니다. 약한 모델은 인용 없이 답할
  수 있고, 그 경우 배지가 "근거 없음"으로 정직하게 표시된다.
- PDF `page` 메타는 페이지별 문자 수 구조가 있는 추출에만 붙는다(OCR 없는
  스캔 PDF는 여전히 저품질 신호로 처리).
- VS Code/Telegram 표면의 승인 흐름은 여전히 갭(✖)이다 — `docs/SURFACE_PARITY.md` 참조.
- 실모델 agent smoke는 게이트가 아니라 주간 리포트다. CI 러너에는 로컬 모델이
  없으므로 정직한 skipped 리포트가 정상이다.

## Verification

- 유닛 테스트 전체 green (신규: run store/loop hardening 35, retrieval policy
  gate/hybrid/freshness, typed chunking 16, grounding bench, promotion review,
  workflow scenarios, funnel soft gate, agent smoke contract).
- agent_eval 23/23, retrieval fusion benchmark gate, brain quality eval,
  golden filegen fixtures, OpenAPI drift, i18n parity, bundle budget,
  docs gates — 모두 통과 후 태그.
- 실모델 스모크: 로컬 gemma 모델로 3태스크 실행, repairs 계측 확인(fail-open).
