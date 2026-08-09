# v11.0.0 — Full Measure (2026-08-10)

10.3.0이 바닥을 측정했고(Measured Ground), 10.4.0이 그 바닥에 이름을
붙였습니다(Named Ground). 11.0.0은 그 궤적의 완결입니다: **출하되는 모든
Python 라인이 테스트 아래에서 실행되고, 단 한 줄이라도 빠지면 CI가
실패합니다.** 기능 릴리스가 아니라 검증 릴리스이며, 화면은 한 픽셀도
의도적으로 바뀌지 않았습니다.

## 숫자

| | 10.10.0 | 11.0.0 |
| --- | --- | --- |
| Python 커버리지 | 72.80% (floor 70) | **100.00% (floor 100)** |
| Python 테스트 | 2,269개 | **5,426개** (+3,157) |
| 측정 대상 문장 | 34,385 | 34,374 |
| 신규 테스트 파일 | — | 145개 (`tests/unit/test_cov_*`) |
| `pragma: no cover` | (기존 관례) | **정확히 8줄, 전부 사유 명시** |
| mypy | 274/274 · 0 에러 | 276/276 · 0 에러 |
| 프론트엔드 커버리지 | 100% (4지표 게이트) | 100% (변화 없음, 1,646 테스트) |

커버리지는 3회 연속 클린 풀런에서 모두 100.00%로 재현됐고, `fail_under =
100`이 기존 70% 게이트가 있던 CI 스텝에 그대로 들어갔습니다.

## 어떻게 올렸나

- **서브시스템 단위 작업 패키지 31개.** 9,354개 미커버 라인을 파일·라인
  단위 브리프로 나눠 마감했습니다. 라우터는 실제 팩토리에 페이크를 주입해
  세우고(`tests/unit/test_auth_router.py` 관례), 그래프·스토리지는
  `tmp_path` 위의 **실제 SQLite 스토어**로, 문서 파서는 제품이 쓰는 그
  라이브러리로 만든 **실제 pptx/pdf/docx/xlsx 입력**으로 검증합니다.
- **플랫폼 잠금 분기도 CI에서 실행됩니다.** MLX·Windows 프로브·watchdog·
  reportlab·psycopg 같은 OS/하드웨어 의존 경로는 `sys.modules` 페이크와
  시임 패치로 구동되어, ubuntu 커버리지 레그가 맥과 같은 100%를
  측정합니다. 어떤 플랫폼도 조용히 제외되지 않습니다.
- **결정성.** 슬립·실제 네트워크·실제 서브프로세스·벽시계 의존 없이
  작성됐고, 전체 스위트를 3회 연속 돌려 100.00%가 매 실행 재현됨을
  확인했습니다.
- **부수효과 커버리지 제거.** `build_phases`의 후반 4개 페이즈(~104문)는
  `test_security.py`의 `from server import ...` 부수효과로만 커버되고
  있었습니다. 이제 각 페이즈를 손으로 구성한 `RuntimeContext`로 직접
  구동하는 전용 테스트가 있어, 그 임포트가 좁아져도 게이트는 버팁니다.

## pragma 8줄 — 전수 목록

라인 커버리지에서 제외된 코드는 아래 8줄이 전부이며, 각 줄이 도달 불가
사유를 주석으로 지닙니다.

1. `lattice_brain/graph/curator.py:199` — 직전 줄에서 `set()`으로 중복
   제거된 목록을 다시 걷는 가드.
2. `lattice_brain/graph/proactive.py:473` — `callable(None)`이 False라
   진입 불가한 mypy 내로잉 가드.
3. `lattice_brain/graph/retrieval.py:165` — 같은 SQL 윈도우에서 노드와
   엣지를 뽑으므로 성립 불가한 끝점 가드.
4. `lattice_brain/runtime/agent_runtime.py:453` — 항상 20자를 넘는 접두사에
   대한 길이 가드.
5. `latticeai/api/models.py:400` — 위에서 모든 허용 접두사를 처리한 뒤의
   폴백 raise.
6. `latticeai/models/router.py:198` — 위 분기가 먼저 반환하는 섀도잉된
   `startswith` 분기.
7. `latticeai/runtime/access_runtime.py:121` — 빌드 타임에 동결되는 불리언
   보수(complement) 관계상 도달 불가.
8. `latticeai/services/memory_service.py:1137` — `"workspace"`가
   `WORKSPACE_KINDS`의 원소라 by-kind 분기가 항상 선점하는 섀도잉 분기.
   순서를 바꾸면 `clear()`가 지우는 범위가 넓어지므로 동작 변경 없이
   기록만 합니다(아래 관찰 사항 참조).

## 커버리지가 찾아낸 것 — 관찰 사항 (고치지 않고 기록)

3,157개의 테스트를 쓰는 과정은 실제 결함과 죽은 분기를 드러냈습니다.
이 릴리스는 검증 릴리스이므로 **동작을 바꾸지 않았고**, 각 항목은 현재
동작을 그대로 단언하는 테스트로 고정되어 있습니다. 수정은 다음 릴리스의
후보 목록입니다.

- `telegram_bot.py:547` — `send_web_link`가 Telegram `sendMessage`를 로컬
  서버용 bearer 클라이언트로 보내, 서버 세션 토큰이 api.telegram.org로
  향하고 토큰 미설정 시 `/web`이 실패합니다.
- `telegram_bot.py:1415` — 모델 전체 언로드가 개별 delete 실패 후에도
  `status_code: 200`을 합성해 "완료"로 보고합니다.
- `tools/filesystem.py:375` — `inspect_html`의 스타일시트 수집이 절대
  발화하지 않습니다: `" ".join(rel)`이 문자열을 문자 단위로 벌려
  `"stylesheet"` 매치가 불가능합니다.
- `core/workspace_review_items.py:45` — 리뷰 아이템 id가 초 해상도
  타임스탬프 해시라, 같은 초에 같은 제목/출처/종류/유저로 두 건을 만들면
  id가 충돌해 두 번째가 영구히 가려집니다.
- `services/model_engines.py:281-292` — vLLM 이전 프로세스가 terminate를
  무시하면 kill 후 재수확(wait)이 없어 좀비의 `poll()`이 None → "이미 실행
  중"으로 오판하고 새 서버 없이 성공을 반환합니다.
- `api/security_dashboard.py:444-446` — 업로드 파일 **목록**이
  `content_preview`만 마스킹하고 `extracted_text` 등은 원문으로 반환합니다
  (상세 API는 마스킹함).
- `api/security_dashboard.py:495-523` — 직렬화된 JSON 문자열 위에서
  레드액션이 돌아, 시크릿이 실제로 있던 페이로드는 invalid JSON으로
  방출됩니다.
- `core/embedding_providers.py:334,345` — MLX 임베딩의 `model_id`가 기본
  차원으로 동결된 채 첫 호출에서 실제 차원으로 변이해, KG 인덱스 정체성에
  쓰이는 식별자와 메타데이터가 어긋날 수 있습니다.
- `api/chat_stream.py:15-21` — fast-path 응답의 `X-Model` 헤더와 SSE 본문
  `model` 필드가 서로 다른 모델명을 보고합니다.
- `api/workspace.py:674,765-776` — 스프레드가 `status: "ok"`를 항상
  덮어쓰는 응답, 그리고 권한 검사가 선행해 404가 아닌 403으로만 답하는
  미도달 404 분기.
- 죽은 코드 후보: `core/tool_registry._wa()`(호출처 0곳),
  `workspace_os_utils._snapshot_graph_import_payload`(미사용).

## 검증

| gate | result |
| --- | --- |
| pytest (full, coverage) | 5,426 passed · **100.00%** (floor 100) — 3회 연속 재현 |
| vitest (coverage) | 1,646 passed (89 files) · 100% 4지표 |
| mypy | 276 / 276 modules, 0 errors |
| ruff | All checks passed (신규 테스트 145파일 포함) |
| agent_eval | 23 / 23 (success rate 1.0) |
| brain quality eval | recall@5 0.95 · must-include 1.0 (corpus fixture) |
| syntax discovery | 1,537 modules OK |

## 정직한 한계

- **라인 커버리지이지 분기 커버리지가 아닙니다.** `branch = true`는 다음
  단계입니다. 몇몇 데이터 의존 False-분기(예: 전 항목이 multimodal인 모델
  카탈로그)는 라인으로는 덮여도 분기로는 열려 있습니다.
- **100%는 정확성 증명이 아닙니다.** 위 관찰 사항이 그 증거입니다 — 어떤
  테스트는 버그가 있는 현재 동작을 그대로 단언합니다. 100%가 보장하는 것은
  "모든 라인이 실행되고 관찰된다"이지 "모든 라인이 옳다"가 아닙니다.
- 발견된 결함들은 이 릴리스에서 수정되지 않았습니다. 검증 릴리스가 동작
  변경을 실어 나르면 두 변화가 서로를 가리기 때문입니다.
- `tests/unit/test_security.py`의 `from server import ...`는 여전히 수집
  시점에 전체 런타임을 실제 데이터 디렉터리에 조립합니다(이번 작업 이전부터
  있던 스위트 동작). 커버리지가 더는 거기에 의존하지 않지만, 그 임포트
  자체는 다음 정리 후보입니다.

## Artifacts (exact filenames)

- `dist/ltcai-11.0.0-py3-none-any.whl`
- `dist/ltcai-11.0.0.tar.gz`
- `ltcai-11.0.0.tgz`
- `dist/ltcai-11.0.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.0.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다. 패키지 스토어 배포는 owner-run입니다.
