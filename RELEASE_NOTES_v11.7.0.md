# Lattice AI v11.7.0 — Clean Sweep (2026-08-15)

11.6.0이 공개로 남긴 구멍을 닫고, 그 과정에서 드러난 회귀를 고치고,
표면을 한 언어로 다시 그렸습니다. 문은 그대로입니다 — `lattice-host`가
**420 오퍼레이션 / 41 패밀리**를 서빙하고, Python은 **28 라우트의 AI
워커**이며, 워커 allowlist(`rust/fixtures/worker_allowlist.json`)는
`/worker/parse`와 `/worker/render/*`를 포함한 채 그대로입니다.

## 한 줄 요약

| | |
|---|---|
| 테마 | **Clean Sweep** — 백로그 제로 + 전면 시각 재설계 |
| 11.6.0 §5.2 | 오라클 버그 3건을 **실제로** 고침 (픽스처는 의도적 발산) |
| 11.6.0 §5.3 | 업로드 파싱 · 청크 벡터 · 훅 발화 · sanitize · 리뷰 이벤트 · `workspace_os.json` 단일 writer |
| 몰랐던 회귀 | Self-Model 쓰기 전면 정지 · xlsx 보안보내기 502 · chat/vault-watch 좌초 시임 |
| 시한폭탄 | chronicle/briefing/insights/garden/proactive/health/quality 시계 시임 |
| UI | 먹빛 밤 / 한지 낮, elevation 사다리, 유리 없음, 번들 ~103 KiB gzip |

## 1. 11.6.0 §5.2 — 알면서 이식한 오라클 버그, 이번에는 고쳤습니다

바꾸면 표면이 달라지므로 11.6.0은 Python이 하던 그대로 옮기고 노트에
적었습니다. 11.7.0은 그 세 곳을 고치고, 골든이 옛 고장을 고정하고
있었으면 **픽스처를 의도적으로** 바꿨습니다.

- **`GET /api/command/search` knowledge 그룹이 결과를 돌려줍니다.**
  오라클은 `keyword_search`의 `matches`를 `results`로 읽고 항상 `[]`를
  만들었습니다. 11.6.0은 그 호출을 삭제해 빈 그룹을 고정했습니다.
  키를 `matches`로 읽고, 오라클과 같은 스코프·한도·투영(`id` / 제목
  120자 / 요약 160자 / `type`)을 적용합니다. `memory_brain.json`의
  `search` · `search_korean` · `search_conversation_hit` 세 케이스가
  발산합니다 — 대화 그룹은 바이트 동일(대조군)이고 knowledge 레인만
  채워집니다.
- **리뷰 스누즈가 offset-aware datetime을 받습니다.** `Z` / `+HH:MM` /
  `-05:00` / naive 모두 200. 파싱 불가한 `until`은 쓰지 않고 **422**
  `{"detail":"until must be an ISO-8601 datetime (for example
  2099-01-01T00:00:00+00:00)"}`. 형제는 같은 라우터의 다른 422처럼
  **영문 리터럴**입니다 (`review.snooze_until_invalid` 카탈로그 id는
  후보로만 남김).
- **이미 거절된 제안을 다시 거절하면 409**입니다. 본문은 형제
  `POST /automation/reviews/{id}/dismiss`와 바이트 동일:
  `{"detail":"cannot 'dismiss' a review item in status 'dismissed'"}`.
  픽스처 `#68`이 500 → 409로 발산합니다.

## 2. 11.6.0 §5.3 — 남아 있던 구멍

- **바이너리 업로드 추출.** `/upload/document`는 UTF-8이 아니거나
  `.pdf/.docx/.xlsx/.pptx/.doc/.odt/.epub`이면 기존
  `POST /worker/parse`(`filename` + `content_b64`)를 타고, 파싱된
  텍스트가 extract → embed → chunk 체인을 갑니다. 시임 실패는 오늘과
  같습니다 — Document 노드는 기록되고 HTTP 200, Concept 서브그래프와
  공급 벡터는 눈에 보이게 없습니다. 새 워커 라우트·allowlist 변경 없음.
- **공급 벡터가 네 문 모두에서 청크까지 갑니다.** upload · browser-tab ·
  garden note · chat turn이 `{"texts":[…],"kind":"passage"}`로 배치
  임베드하고, `(model_id, dim)`이 네이티브 임베더와 일치할 때만
  붙입니다. `POST /knowledge-graph/ingest`는 계약상 텍스트 전용
  (`type`/`content`/`title`)이라 그대로 둡니다.
- **네이티브 도구에 사용자 훅이 발화합니다.** `HooksStore` 하나와
  `NativeHookSink`(`HookSink`)가 루프 두 구성 지점에 주입됩니다.
  `pre_tool` 블록은 `PermissionError`, `post_tool`은 양쪽 결과에
  발화하고, 런 기록은 `POST /api/hooks/run`과 같은 13키를
  `hooks_runs.json`에 남깁니다(상한 100). 플랫폼 빌트인은 돌리지
  않습니다 — 이미 결정된 일을 advisory로 적지 않기로 한 결정입니다.
- **`sanitize_write_content`가 네이티브 write 경로에 적용됩니다.**
  에이전트 루프의 `write_file`, 도구 디스패치, `POST /tools/write_file`
  모두. Python 골든 5쌍은 바이트 동일, 62벡터 교차검증. `.py` 검증은
  `ast.parse`가 아니라 구조 토크나이저입니다(이 프로세스에 CPython이
  없음). `edit_file` · `local_write` · 지식/옵시디언 저장 · 워커가 만든
  바이너리 렌더는 범위 밖입니다.
- **`review_item_created` / `review_item_updated`가 모든 변이 경로에서
  기록됩니다.** create · approve · dismiss · snooze · unsnooze ·
  bulk/approve · bulk/dismiss · `/api/proposals/{id}/approve|reject` ·
  에이전트 루프 `ProposalStore::create`. `run_now`는 쓰기를 하지 않으므로
  이벤트를 만들지 않습니다. owner가 설치되지 않은 standalone
  `lattice-retrieval`은 침묵합니다 — 타임라인 cap은 주인의 것입니다.
- **`workspace_os.json`은 writer가 하나입니다.**
  `WorkspaceOsStore`(디렉터리 키 레지스트리 + 하나의 락) 뒤에
  GovernanceState · MCP/플러그인 · workflow designer · retrieval
  `StateWriter` 포트 · `JsonProposalStore`의 `DocumentWriter`가
  모입니다. 스탬프는 `CARGO_PKG_VERSION`. 깊은 병합, SQLite 먼저 그다음
  JSON, trailing newline 없음.

## 3. 11.6.0이 몰랐던 회귀

- **Self-Model 쓰기가 100% 죽어 있었습니다.**
  `self_model_upsert/delete/propose/apply`와 `stamp_contradiction`은
  은퇴한 `/worker/graph/mutate`로만 나갔습니다. 배선된 설치에서도
  404입니다. `resolve_contradiction`은 시임 오류를 삼켜 `stamps: []`를
  돌려주고 Review Center는 "적용됨"이라고 말했습니다. 네이티브 포트
  (`self_model_write` + `GraphWriter::{delete_node,stamp_node_validity}`)가
  녹화된 본문 **9건을 바이트 동일**하게 재생합니다. 스탠드인 워커의
  mutate는 트립와이어이고 요청 횟수는 0입니다.
- **xlsx 보안보내기가 502였습니다.** `/tools/create_xlsx`는 이 프로세스가
  마운트하는 **제품** 경로라 워커 시임이 아닙니다. 이제
  `POST /worker/render/xlsx`에 `RenderXlsxRequest`를 보내고
  `content_b64`를 읽습니다. 응답 계약(content-type, disposition)은
  그대로입니다.
- **chat `ingest_generated`와 vault-watch 노트 경로가 좌초해 있었습니다.**
  chat 쪽은 11.5.2 워커에 대해서도 스키마가 안 맞아 **400**이었습니다
  (살아 있는 라우트에 잘못된 본문). 둘 다 네이티브
  extract → embed → `GraphWriter`입니다. vault-watch는 감지와 노트
  쓰기가 따로 있었고, 이 릴리스가 폴러로 이었습니다 —
  `bridge_wired`/`polling`이 살아 있고, 감시 `.pdf`는 `/worker/parse`를
  탑니다.
- **좌초 워커 경로 정적 게이트**(디코이 증명). `src/`의 `/worker/` ·
  `/agent/` 리터럴과 `post_json` 인자를 스캔합니다. 부채 레지스터는
  비어 있습니다. `/worker/graph/mutate` 9곳과 `/tools/create_xlsx` 1곳은
  0입니다.

## 4. 시한폭탄 — 시계 시임

픽스처 스토어의 스탬프는 `2026-08-14T12:00:00`입니다. 월시계를 읽던
임계값은 날짜가 바뀌는 순간 빨개집니다.

- chronicle `@today` / `@ts`는 캡처 날짜·그날 끝으로 팽창. offset이 있는
  provenance 스탬프는 시드 시 naive로 내림 — `Pacific/Kiritimati`에서
  overview가 이틀로 갈라지던 것.
- briefing freshness(`STALE_DAYS=45` → 2026-09-28).
- insights / garden / proactive-brief (`RECENT_DAYS=7` → 2026-08-21).
- health (45일 → 2026-09-28), quality-report (90일 → 2026-11-12).

전부 `BrainState::now_utc()`를 타고, 하니스가 캡처 시각에 얼립니다.
falsifier 테스트가 퓨즈를 다시 무장하면 다음 실행에서 실패합니다.
4개 시간대(UTC−12 … UTC+14)에서 증명했습니다.

## 5. UI/UX — 먹빛 밤, 한지 낮

유리는 없습니다. 깊이는 elevation입니다.

- 토큰: `--elev-0..4` · `--grad-*` · `--border-subtle/strong` ·
  `--warning-ink` / `--danger-ink` · 타입/라디우스/모션.
- 셸 · LivingBrain · 홈 · 대화 · 온보딩 · 그래프 · Capture · Chronicle ·
  Act/Review · Library · System · Admin · 커맨드 팔레트 — 같은 언어.
- Cytoscape는 토큰 색을 쉼표형 `hsl()`로 풀어 씁니다 (테마 전환 시
  재빌드). Color 4 공백 문법은 파서가 거절해 노드가 회색이 됐습니다.
- 레이아웃/a11y 계약은 유지. 번들 **~103 KiB gzip** (150 KiB 예산).
- 스크린 클레임은 13장 전부. 증거 재촬영은 R-B.

## 6. 정직한 고지 — 이번에 닫지 않은 것

- **`open_keys`는 `pending`만.** Python `ProposalDesk`는
  `{pending, snoozed}`입니다. snoozed Self-Model/합성 제안은 여기서
  다시 뜨지 않습니다. `proactive_brief`와 필터를 나누면 픽스처 카운트가
  움직입니다.
- **추출 `refiner` 없음.** 모델이 후보 문장을 다듬는 훅은 호출자가
  없었고, 추가하지 않았습니다.
- **`delete_node`는 `PART_OF` 엣지를 남깁니다.** Python
  `delete_self_model_fact`와 같습니다. 다시 추가하면 엣지를 재upsert
  하므로 매달린 것이지 틀린 것은 아닙니다.
- **owner가 없으면 리뷰 이벤트는 침묵합니다.** standalone
  `lattice-retrieval`에는 타임라인 리더도 없습니다.
- **`POST /knowledge-graph/ingest`는 텍스트 전용** — 계약. 파일 업로드
  문이 아닙니다.
- **리뷰 변이는 스토어 사이클 2회**(아이템 쓰기, 그다음 타임라인
  추가). 200건 벌크는 이전의 약 4배 파일 쓰기입니다.
- **`snooze_until_invalid` detail은 이웃과 같은 영문 리터럴.**
- Telegram 브리지와 SSO OIDC 로그인/콜백은 11.6.0에서 제거된 채로
  남습니다. 설정 표면과 패스워드 로그인은 그대로입니다.
- 여섯 pyautogui 포인터 도구는 워커에서 실행됩니다. 재고 설치에서는
  "unavailable"입니다.

## 7. 게이트

R-A는 버전 정합 · 문서 게이트 ·
`LTCAI_SKIP_RELEASE_EVIDENCE_BOUND=1 npm run lint`를 지킵니다. 증거
재촬영과 산출물 빌드는 R-B, 전체 검증 배터리는 R-C입니다. 통합 시점에
프론트 단위 테스트는 문·분기·함수·라인 100%(약 1,770), Python은
`fail_under=100`, 워커 allowlist는 28입니다.

## 8. 산출물

- `dist/ltcai-11.7.0-py3-none-any.whl`
- `dist/ltcai-11.7.0.tar.gz`
- `ltcai-11.7.0.tgz`
- `dist/ltcai-11.7.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.7.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다.

## 9. 지원 정책

공개 히스토리와 보안 지원의 하한은 **11.0.0**입니다. 11.7.0은 11.6.0과
같은 프로그램 — Rust 제품 서버 + Python AI 워커 — 위에 쌓입니다.
10.x·9.x는 다른 프로그램이며 `SECURITY.md`는 11.x만 지원합니다.
이전 노트는 `RELEASE_NOTES_v*.md`로 트리에 남습니다.
