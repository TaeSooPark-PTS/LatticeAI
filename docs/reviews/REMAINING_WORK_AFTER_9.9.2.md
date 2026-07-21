# 9.9.2 이후 남은 작업 — 리뷰 대비 구현 현황

> **기준 문서:** [FULL_STACK_UX_HARNESS_KG_FILEGEN_REVIEW_2026-07-21.md](FULL_STACK_UX_HARNESS_KG_FILEGEN_REVIEW_2026-07-21.md)
> **작성일:** 2026-07-21 (v9.9.2 Artifact Trust 릴리스 직후)
> **성격:** 리뷰의 권고 항목을 "9.9.2에서 닫힘 / 남음"으로 정직하게 매핑한 백로그.
> 다음 릴리스 계획 시 이 문서에서 골라 내려가면 된다.
>
> **후기 (2026-07-22):** 2절의 22개 항목 전부 v9.9.3 — Closed Loops 로 출하 완료.
> 이 문서는 역사적 기록으로 유지한다.

---

## 1. 9.9.2에서 닫힌 항목 (완료)

리뷰가 "기술적으로 가장 레버리지가 큰 한 방"으로 지목한 Wave 0 계약 고정과
Wave 1 체감 항목 일부를 구현했다.

| 리뷰 항목 | 리뷰 위치 | 구현 |
| --- | --- | --- |
| ArtifactWritePipeline — 모든 write_file 검증 단일화 | §2.2 L1, §6.3, §8.5 Phase A, Wave 0 | `sanitize_write_content` + `agent.py` `_dispatch_step` 배선, transcript `content_sanitize`, trace `artifact_sanitize`/`artifact_repair` |
| HTML 검증 강화 (fence/prose 래핑을 유효로 오판하던 문제) | §8.3 #2 파생 | `validate_file_content` 문서 시작 앵커 + fence 거부 |
| artifacts[] 응답 계약 | §3.3 P1, §8.4 | direct path + agent HTTP 둘 다 `artifacts[]` (kind/previewable/valid/repaired), `collect_artifacts()` |
| 재생성 overwrite 방지 (자동 접미사) | §8.3 #8 | `next_available_path` — `generated_page_2.html`식, 응답에 안내 문구 |
| 생성 파일 자동 Brain 재수집 | §7.2 A P0 | `IngestionPipeline` 훅 (`workspace://` provenance, `LATTICEAI_INGEST_GENERATED=0`으로 끔, 실패해도 파일 생성은 성공) |
| Plan 최소 스키마 + heuristic plan | §6.3 L3 | `normalize_plan` — goal/steps 강제, 빈 plan + 파일 인텐트 → write_file 1-step |
| Memory 루프 품질 (trivial 학습 억제) | §6.3 L5 일부 | `filter_learnings` — 길이/패턴/중복 필터 |
| repaired 배지 (정직 배지) | §4.2, §11 리스크 | 파일 카드 "자동 보정됨" 배지 (ko/en, 다크/라이트) |
| DONE vs NEEDS_REVIEW/FAILED 시각 구분 | §2.3, §4.2, Wave 0 | `AgentStateNote` 경고 스트립 (`role="alert"`, 파일 없어도 표시) |
| FG 하네스 (FG-01..FG-08) | §5.2 H1, Wave 3 일부 | `tests/unit/test_artifact_write_scenarios.py` 17 테스트 — CI 게이트 |
| product_readiness에 파이프라인 증거 연결 | Wave 0 | `action-aware-chat` 게이트 evidence 확장 |

---

## 2. 남은 항목 (우선순위순 백로그)

### P1 — 다음 릴리스 최우선 후보

| # | 항목 | 리뷰 위치 | 내용 / 비고 |
| --- | --- | --- | --- |
| 1 | **Artifact Loop (멀티파일 manifest)** | §6.3 L2, §8.5 Phase C, Wave 3 | `infer_project_manifest` → 파일별 generate/validate → 묶음 쓰기 → 번들 검증(링크/참조) → zip 다운로드. 현재는 `create_web_project` 스캐폴드만 있음. "todo 앱 html+css+js" 요청 체감의 핵심. |
| 2 | **WAITING_APPROVAL 대화형 승인 UI** | §6.3 L4, Wave 3 | 현재 human 없는 승인 필요 작업은 FAILED로 끝남(안전하지만 UX 단절). `status=awaiting_approval` + plan 요약 + 인라인 "승인하고 실행/수정해서 실행" + 짧은 TTL 승인 토큰. `resume` API 골격은 이미 있음. |
| 3 | **First Value Loop (30초 가치 루프)** | §3.3 P0, Wave 1 | 내장 demo corpus 3문서 원클릭 주입 → 미리 채워진 질문 → 출처 하이라이트 → HTML 파일 생성까지 고정 스크립트. FirstFiveCard는 있으나 "샘플 노트 → 회상 성공" 트랙이 없음. |
| 4 | **파일 카드 인라인 미리보기** | §3.3 P1, §4.2 | `artifacts[].previewable`는 이미 내려감. HTML sandbox iframe(+CSP), md/txt/json 모달 렌더. 현재는 다운로드만. |
| 5 | **retrieval fusion + 벤치 임계값 게이트** | §7.2 E, Wave 2 | 쿼리 클래스별(사실/코드/사람/최근) 가중치 + `retrieval_benchmark_fixtures` CI 임계값(회귀 시 fail). brain_quality_eval은 있으나 fusion 튜닝/게이트는 없음. |

### P2 — 파이프라인/자동화 체감

| # | 항목 | 리뷰 위치 | 내용 / 비고 |
| --- | --- | --- | --- |
| 6 | automation dry-run + 실행 로그 표면화 | §7.2 F, Wave 2 | 설치 직후 "지금 한 번 실행" 버튼, 마지막 실행 결과를 Act/브리핑에, 실패는 Review 큐로. |
| 7 | folder job 완료 리포트 카드 | §3.3 P1, §7.2 B, Wave 1 | "+N documents, +M chunks, vector x% fresh" + 스킵/실패/저품질 샘플 3개. jobs API는 있고 카드 UI가 없음. |
| 8 | 폴더 watch mode (옵트인) | §7.2 B | FSEvents/watchdog 증분 인덱싱. 명시 동의 UI 필수, 기본 off (리뷰 리스크 섹션). |
| 9 | 웹 추출 품질 CTA / 재캡처 | §7.2 C, Wave 2 | 추출 빈약 시 원문 하이라이트·재캡처·수동 붙여넣기 유도. 품질 스키마는 파이프라인과 동일하게. |
| 10 | graph curator 노이즈 감소 job | §7.2 D, Wave 2 | 휴리스틱 concept 노드 IDF/빈도 컷, 관계 동사 정규화 사전(ko/en 매핑). |
| 11 | 인용 강제 (answer without source → "근거 없음" 표시) | §7.2 E | context_quality는 있으나 답변-인용 바인딩 강제는 없음. |

### P3 — 하네스/관측 성숙

| # | 항목 | 리뷰 위치 | 내용 / 비고 |
| --- | --- | --- | --- |
| 12 | agent_eval에 filegen 시나리오 통합 | §5.2 H2, Wave 3 | 더러운 write_file content → sanitize → critic PASS 시나리오를 scripted 게이트에 추가 (현재는 unit 테스트로만 커버). |
| 13 | 주간 멀티모델 filegen 리포트 | §5.2 H1, §8.5 Phase D | `scripts/bench_models.py` 연동 — 실제 로컬 모델(gemma/qwen/llama) × 파일타입 성공률, fail-open 리포트 (CI 필수 아님). |
| 14 | Regression golden files | §5.2 H4 | `tests/fixtures/filegen/` dirty 출력 → 기대 클린 파일. 프롬프트 변경 시 골든 갱신 리뷰 필수. |
| 15 | Knowledge Pipeline E2E 결정론 하네스 | §5.2 H3 | temp 폴더 → ingest_folder → hybrid search → chat context_quality → suggestions 필드까지 한 줄 E2E. |
| 16 | UX 퍼널 메트릭 수집 | §4.3 | TTFV, file request→real file rate(>95%), code-only rate(<2%), NEEDS_REVIEW rate 추적. product_readiness에 1개 연결됨, 런타임 메트릭은 없음. |

### P4 — Polish (Wave 4)

| # | 항목 | 리뷰 위치 | 내용 / 비고 |
| --- | --- | --- | --- |
| 17 | a11y 전면 감사 (focus trap, graph 키보드, reduced-motion) | §4.2, Wave 4 | 부분 적용 상태. |
| 18 | 전역 드래그앤드롭 capture | §3.3, §4.2, Wave 4 | Brain home 전역 hit-target, Capture 페이지 의존 감소. |
| 19 | proposal conflict(409) rebase UX | §4.2, Wave 4 | 409 시 "다시 읽어서 재적용" 흐름. 백엔드 409는 9.9.0에서 완료. |
| 20 | 감정 디자인 마감 (성공 펄스/유입 파티클) | §3.3 P2 | prefers-reduced-motion 존중 전제. |
| 21 | Phase budgets 분리 (plan/execute/verify 토큰 예산) | §2.3 | 약한 모델이 plan에 토큰을 소진하지 않게. |
| 22 | `.tsx`/`.vue`/`.svelte` 등 파일 생성 확장자 확대 + Python `ast.parse` 검증 | §2.3 filegen 표 | 현재 html/json/css/py/js/기타 텍스트 중심. |

---

## 3. 의도적으로 하지 않은 것 (비목표 — 리뷰 §11과 동일)

- 클라우드 기본화, 그래프를 홈 주 UI로 복귀, 검증 없는 자율 삭제/덮어쓰기,
  모든 파일 타입 OCR/멀티모달 일괄 지원.
- `/tools/write_file` 사용자 직접 호출 경로의 sanitize — 사용자가 명시한
  콘텐츠는 신뢰하며 그대로 저장한다(모델 출력만 untrusted).

## 4. 다음 릴리스 추천 묶음

리뷰의 AGENTS.md 정렬 순서(§10)를 따르면:

1. **9.10.0 후보:** #1 Artifact Loop + #4 인라인 미리보기 + #12 agent_eval 통합
   (파일 생성 스토리 완결)
2. **그다음:** #2 승인 UI + #3 First Value Loop (신뢰·온보딩 체감)
3. **그다음:** #5 retrieval fusion 게이트 + #6/#7 자동화·폴더 리포트
   (지식 파이프라인 강도)
