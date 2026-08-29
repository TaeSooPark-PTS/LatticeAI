# Lattice AI v11.9.0 — Working Order (2026-08-17)

> **Status: historical** — point-in-time release note.

11.8.0은 부르는 사람 없는 표면을 덜어냈습니다. 11.9.0은 그 다음 — 문서에는
Current 인데 문이 스텁이거나, 라이브 Brain에서 500을 내거나, 클라우드가
반만 배선되어 있던 자리 — 를 작동 순서로 맞춘 릴리스입니다. 문은 그대로
입니다. `lattice-host`가 **420 오퍼레이션 / 41 패밀리**를 서빙하고,
Python은 **19 라우트의 AI 워커**이며, `/worker/sysinfo`에
`capabilities`와 `python_version`만 가산했습니다.

| | |
|---|---|
| 테마 | **Working Order** — 문서에만 있던 기능을 실동작으로 올리고, 라이브에서 깨진 것을 고친 릴리스 |
| 스텁 → 실동작 | 문서화된 Current 13개가 실제로 답함. 라이브 감사 22항목 재검증 |
| 클라우드 | ReviewSink·EgressAudit 프로덕션 바인딩. `api_key`(모의만) + `cli_oauth`(`agy`/`grok`, 과금 0원) |
| MCP | `POST /mcp` streamable-HTTP JSON-RPC 실서버. OpenAPI 계약 밖(설계) |
| 2B · 파일 생성 | gemma-4-e2b compact 프로파일. 「index.html 만들어줘」가 실제 파일을 만듦 |
| 게이트 | cargo **1,917** · pytest **1,168**(실측 99.97%, 바닥 90) · vitest **1,800 / 103 파일** |

## 한 줄 요약

문서에 Current 로 적혀 있던 기능이 실제로 돌아가고, 클라우드와 MCP가
배선되며, 8GB 기본 모델(gemma-4-e2b)로 채팅 파일 생성이 다시 됩니다.
닫지 않은 여섯 가지는 아래 정직한 고지에 그대로 적었습니다.

## 전 기능 실동작 감사와 22건 수리

문서와 라이브 제품이 어긋난 자리를 먼저 닫았습니다. 13개가 스텁에서
실응답으로 바뀌었고, 라이브 감사에서 깨진 N1–N9와 이전에 고장난
22항목을 신선한 Brain·실모델로 다시 통과시켰습니다.

실동작이 된 13개:

- `/models/recommendations` — 네이티브 RAM/AS 프로브 + 워커 카탈로그 +
  RAM-tier `top_pick`.
- `/setup/scan` + `/setup/auto` — 실프로브. `/setup/install`은 실설치
  또는 수동 안내(brew/pip는 설계상 수동).
- computer-use 상태 — 워커 `/worker/sysinfo`의 `capabilities`
  (`pointer_tools`) 프로브.
- `/agent/eval` — 결정적 스킬 평가. 모델이 필요하면 `requires_model`을
  정직하게 말함.
- `/agents/api/run` — 라이브 단일 에이전트 패스 + 정직한 health.
- 자동화 패턴/제안 — `conversation_messages` 위 결정적 한국어 친화
  질문 마이닝.
- 워크플로 run — 스텝 단위 실행기, 종료 상태. resume의 승인 게이트를
  고쳤고, 리뷰 `run_now`가 같은 실행기에 붙음.
- `build` / `deploy_project` — 거버넌스된 스크립트를 실제로 실행.
- 백업 blob.

라이브 감사 N1–N9:

- 에이전트 루프가 호스트에 묶이고, run 본문이 실제 정책 표를 싣습니다.
- 메모리 API가 빈 Brain에서 500을 내지 않습니다 —
  `conversation_messages`를 부트스트랩하고 리더가 방어적으로 읽습니다.
- chat / memory / chronicle / command가 지식을 봅니다. workspace가
  null이면 personal 가시성이고, writer는 `"personal"`을 찍습니다.
- brain health가 측정할 것이 없을 때 빈 100점을 주지 않습니다.
- 백업은 `VACUUM INTO` 스냅샷 + blob + 정직한 매니페스트 + 원자적
  복원입니다.
- export가 edges/chunks를 싣습니다(없던 컬럼을 조회하던 버그).
- 폴더 ingest가 신뢰된 소유자와 통합 승인 토큰을 받습니다.
  `LocalApprovals`는 `/permissions/approve`에서 상환됩니다.
- 보이스 메모 텍스트가 저장됩니다.

`/worker/sysinfo`는 가산으로 `capabilities`(pointer_tools)와
`python_version`을 답합니다. 라우트 수는 19입니다.

## 하이브리드 클라우드 완성: OAuth CLI 이중 자격증명과 에스컬레이션

클라우드는 선택입니다. 기본은 로컬이고, 경계 다이얼이 `cloud_allowed`일
때만 지식의 최소 슬라이스가 나갑니다. 그래프 자체는 나가지 않습니다.

**싱크가 프로덕션에 묶였습니다.** ReviewSink와 EgressAudit이 호스트에
바인딩됩니다. 클라우드 답은 Review Center에 `kg_cloud_expansion` 제안으로
올라가고, egress 감사는 형태만 기록합니다 — provider / model / reason.
내용은 기록하지 않습니다.

**이중 자격증명:**

- `api_key` — OpenAI 호환 HTTP 어댑터. 클라우드 모델은 provider
  설정에서 오고, 로컬 MLX id를 쓰지 않습니다. **모의 서버로만
  검증했습니다.** 이 릴리스에서 실과금 호출은 없습니다.
- `cli_oauth` — 로컬에서 OAuth 인증된 CLI. Antigravity `agy` →
  gemini-3.7-flash(기본), `grok` → grok-4.6. 중립 temp cwd에서
  spawn하고 120초 타임아웃이며, 실패는 정직하게 말합니다.

해석 순서: `cloud_provider.json` → env 키 → `agy` → `grok` → none.
로컬 모델이 없어도 클라우드 턴이 가능합니다. 스트림은 도착하는 대로
나가고, `stream: false`는 JSON입니다.

**에스컬레이션 정책** (`hybrid_policy.json` `escalation`):

- `auto`(기본) — 로컬 모델 없음 / 로컬 컨텍스트가 얇음(매칭 노드 2개
  미만) / 명시적 `/cloud ` 또는 `클라우드:` 접두사.
- `manual` — 접두사만.
- `always` — 경계가 허용하면 항상.

이유는 egress 감사와 hybrid_context 프레임에 남습니다. 요청의
`network_mode:"local_only"`는 항상 이깁니다.

**라이브 OAuth E2E는 API 과금 0원으로 검증했습니다.** `agy`/
gemini-3.7-flash가 답하고 지식 제안 + egress 레코드를 올렸고,
`grok`/grok-4.6가 답했으며, 로컬에 근거가 있는 질문은 로컬에 남고,
`/cloud` 접두사는 `explicit_request`로 올라갑니다.
`GET /api/cloud/status`는 `{configured, mode, provider, model, detail}`
입니다.

SPA는 ☁️ 클라우드 답변 칩(provider/model, 보낸 기억 n개, 지식 제안
m건 검토 대기), 컴포저 경계 힌트, 「이번 대화는 로컬만」 토글,
System 패널의 provider 상태 행(미설정이면 조용히)을 그립니다.

## MCP 실서버

`POST /mcp`가 streamable-HTTP JSON-RPC 서버입니다. 메서드는
`initialize` / `tools/list` / `tools/call`입니다. 큐레이트된 안전 도구
집합과, 파싱된 스키마를 가진 스킬 7개를 노출합니다. 거버넌스 거절은
성공한 도구 결과가 아니라 JSON-RPC 에러입니다. `/mcp/call`이 실제로
디스패치하고, `/mcp/install`은 정직합니다 — 스킬/플러그인을 켜고,
원격은 수동이 필요하다고 말합니다. `docs/mcp-tools.md`가 그 표면을
적습니다.

`/mcp`는 OpenAPI 제품 계약 밖입니다. 설계입니다.

## 2B(gemma-4-e2b) 강건화와 채팅 파일 생성 복원

8GB 티어 기본 모델 `gemma-4-e2b-it-4bit`가 실제로 돕니다. 프로파일
정규식이 `e2b`/`e4b`를 compact로 보고, MoE `a4b` 마커는 건드리지
않습니다. 내장 기본 프롬프트는 워크드 예제를 앞에 두고, 인자 시그니처가
있는 도구 목록과 확장자 앵커를 싣습니다.

파서 사다리는 `tag_strip`(`<|channel|>` 프레이밍) / `balanced` /
`truncated_close` / `labeled` / `fence_rescue`를 더했습니다.
v10.8.0 salvage 삼종(스코어링, 반복 답 거부, bounded regeneration)을
되돌렸습니다. COMPACT 컨텍스트는 2k 토큰 미만이고, EXECUTE 온도는
고정입니다(compact 0.1 / standard 0.2). MLX가 temperature와 stop
string을 존중합니다. `write_file`은 없거나 빈 content를 교정 에러로
거절합니다. critic은 verdict-shape 파싱과 보수적 토큰 단으로 복구하고,
검증할 수 없으면 여전히 fail-closed입니다.

채팅 파일 생성은 v9.2.0 헤드라인이었고 11.6.0 포트에서 빠졌습니다.
「index.html 만들어줘」가 다시 모델이 쓴 실제 파일을 만듭니다 —
확장자별 앵커 프롬프트, judge + 1회 교정, sanitize 라벨이 정직
(valid / repaired → SPA 배지), 프로젝트는 최대 3파일을 순차로, 실제
docx/pdf는 워커 렌더 시임(산문을 docx 바이트로 속이지 않음), xlsx/
pptx는 에이전트 경로를 이름으로 거절합니다. 2B로 라이브 증명:
`index.html`은 `<!doctype html>`로 시작하고, `clock.js`는
`node --check`를 통과하며, `minutes.docx`는 실제 OOXML입니다.

에이전트 루프의 *내용 품질*은 아래 정직한 고지대로입니다. 파일을 쓰는
것과 요약을 통과하는 것은 다릅니다.

## 학습 루프·백업 무결성

`POST /agent`가 네이티브 루프에 닿고 제품 정책 표를 싣습니다.
`/agent/eval`은 결정적 스킬 평가이고, 모델이 필요하면 그렇다고 말합니다.
`/agents/api/run`은 라이브 단일 에이전트이며 health를 과장하지 않습니다.

백업은 `VACUUM INTO` 스냅샷에 blob과 정직한 매니페스트를 붙이고,
복원은 원자적입니다. export가 edges와 chunks를 싣습니다. 오래 떠 있는
프로세스에서 복원하면 커넥션이 재활용되기 전까지 복원 전 바이트를 줄
수 있어서, 복원 뒤에는 재시작이 필요합니다 — 아래 고지에 적습니다.

폴더 ingest는 신뢰된 소유자와 통합 승인 토큰을 받습니다.

## 정직한 고지

- **2B 에이전트 루프의 내용 품질은 정직하게 게이트됩니다.**
  gemma-4-e2b는 요청한 파일을 기계적으로 씁니다(마감 테이프 5/5).
  그 요약은 critic에서 떨어져 FAILED/NEEDS_REVIEW로 끝날 수 있습니다.
  퍼널은 에이전트 런에 더 큰 모델을 권합니다.
- **`api_key` 클라우드 경로는 모의 서버만 검증했습니다.** 계약
  테스트는 있고, 실호출은 없습니다. 과금 예산이 없어서 그렇게
  했습니다. 라이브로 검증한 것은 `cli_oauth`입니다.
- **오래 떠 있는 프로세스에서 복원하면 커넥션이 재활용되기 전까지
  복원 전 바이트를 줄 수 있습니다.** 복원 뒤에는 재시작하십시오.
- **brew/pip 셋업 항목은 설계상 수동입니다.** `/setup/install`이
  그 사실을 숨기지 않습니다.
- **`/mcp`는 설계상 OpenAPI 제품 계약 밖입니다.** JSON-RPC 서버는
  실동작합니다.
- **DMG는 ad-hoc 서명(=미서명)입니다.** 이전 릴리스와 같습니다. 첫
  실행에서 Gatekeeper 우회 절차가 필요합니다.

11.8.0이 열어 둔 항목은 그대로입니다 — 커버리지 강제 바닥은 라인 90,
멀티모달 이미지/비디오 절반에 HTTP 문이 없고, Self-Model `open_keys`는
`pending`만, 추출 `refiner` 없음, `delete_node`는 `PART_OF`를 남김,
owner 없는 리뷰 이벤트는 침묵, KG-api ingest는 텍스트 전용, 리뷰 변이는
스토어 사이클 2회. Telegram 브리지와 SSO OIDC 로그인/콜백은 11.6.0에서
제거된 채로 남습니다. 여섯 pyautogui 포인터 도구는 여전히 워커에서
실행되고, 재고 설치에서는 "unavailable"입니다.

## 게이트

- cargo **1,917** 통과. `cargo clippy --workspace --all-targets --
  -D warnings` 깨끗.
- pytest **1,168** 통과. 실측 커버리지 **99.97%**, 강제 바닥은 라인
  90.
- vitest **1,800** 테스트 / **103** 파일.
- product-readiness **COMPLETE 10/10**.
- lint 통과.
- 수용 매트릭스: 신선한 Brain·실모델에서 이전에 고장 나 있던 22항목
  재검증.

## 산출물

- `dist/ltcai-11.9.0-py3-none-any.whl`
- `dist/ltcai-11.9.0.tar.gz`
- `ltcai-11.9.0.tgz`
- `dist/ltcai-11.9.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.9.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다.

## 지원 정책

공개 히스토리와 보안 지원의 하한은 **11.0.0**입니다. 11.9.0은 11.8.0과
같은 프로그램 — Rust 제품 서버 + Python AI 워커 — 위에 쌓입니다.
10.x·9.x는 다른 프로그램이며 `SECURITY.md`는 11.x만 지원합니다. 이전
노트는 `RELEASE_NOTES_v*.md`로 트리에 남습니다.
