# v11.0.1 — Both Branches (2026-08-10)

11.0.0은 모든 라인을 테스트 아래 놓고, 그 과정이 드러낸 결함 11건을 고치지
않은 채 기록했습니다 — 검증 릴리스가 동작 변경을 실어 나르면 두 변화가
서로를 가리기 때문입니다. 11.0.1은 그 장부의 정산입니다: **기록된 결함
11건 전부 수정, 증명된 죽은 코드 제거, 그리고 CI 플로어가 라인에 더해
분기(branch) 아크 100%까지 잡습니다.**

## 숫자

| | 11.0.0 | 11.0.1 |
| --- | --- | --- |
| 커버리지 게이트 | 라인 100% | **라인 + 분기 100%** (`branch = true`) |
| 분기 아크 | 측정 안 함 | **9,828개 전부 실행** |
| Python 테스트 | 5,426개 | **5,798개** (+372) |
| 기록된 결함 | 11건 (미수정 고지) | **11건 전부 수정** |
| `pragma: no cover` | 8줄 | 8줄 (불변) |
| `pragma: no branch` | — | **2줄, 전부 사유 명시** |

## 결함 11건의 정산

각 수정은 결함을 고정하던 테스트를 **고친 동작의 단언으로 뒤집고**, 수정
자체를 증명하는 회귀 테스트를 추가했습니다 (`tests/unit/test_fix_v1101_*`).

1. **Telegram `send_web_link`** — 로컬 서버용 bearer 클라이언트로
   api.telegram.org를 호출하던 것을 전달받은 Telegram 클라이언트로 교정.
   서버 세션 토큰이 더는 외부로 나가지 않고, 토큰 미설정 환경에서도 링크가
   전송됩니다.
2. **Telegram 전체 언로드** — 개별 delete 결과를 버리고 200을 합성하던
   것을 실결과 집계로: 전부 성공 시에만 기존 완료 메시지, 실패가 있으면
   실패 모델·코드 목록과 성공/실패 개수를 정직 보고.
3. **`inspect_html` 스타일시트 수집** — `" ".join(str)`이 문자를 벌려 영원히
   매치 불가하던 rel 검사를 토큰 분해(`rel.lower().split()`)로 교정 —
   `rel="stylesheet preload"`, 대소문자 변형 포함 실제로 수집됩니다.
4. **리뷰 아이템 id 충돌** — 초 해상도 타임스탬프 해시가 같은 초 동일
   입력에서 충돌해 두 번째 아이템을 영구 섀도잉하던 것을, 기존 id와 충돌
   시 시퀀스 재해시로 교정(무충돌 경로는 바이트 동일).
5. **vLLM 좀비 오판** — kill 후 wait 재회수를 추가하고, 그래도 살아 있으면
   침묵 성공 대신 409로 정직하게 실패. 침묵 성공을 만들던 재확인 분기는
   제거. llama.cpp도 같은 규칙으로 정렬(공용 `_reap_local_server`).
6. **보안 대시보드 파일 목록** — `content_preview`만 마스킹하던 것을
   구조 보존 워커(`_redact_structure`)로 모든 문자열 필드
   (`extracted_text`, 중첩 리스트 포함) 마스킹. 저장소 원본은 불변(복사본
   마스킹).
7. **보안 대시보드 상세/내보내기** — 직렬화된 JSON 문자열 위에서 돌던
   레드액션이 닫는 따옴표를 소비해 invalid JSON을 방출하던 것을, 직렬화
   **전** 구조 마스킹으로 교정 — 시크릿이 있어도 항상 유효한 JSON. 같은
   결함이 있던 txt/csv 내보내기도 함께. 후속으로 상세 라우트
   (`/admin/security/files/{id}`)도 목록과 동일 규칙으로 정렬(수정 전에는
   목록이 상세보다 엄격해지는 역전이 생겼기 때문).
8. **임베딩 `model_id` 차원 동결** — MLX 프로바이더가 기본 차원(:384)으로
   id를 동결한 채 실측 차원으로 뒤늦게 변이하던 것을, 생성 시
   `_guess_dim` + 실측 시 `model_id` 동기화로 교정. 잘못된 id로 만들어진
   기존 인덱스는 이제 `stale_embedder`를 정직하게 신고합니다.
9. **fast-path 모델 표기 불일치** — `X-Model` 헤더와 SSE 본문 `model`이
   다른 이름을 보고하던 것을 콜사이트 한 줄로 일치시킴.
10. **워크스페이스 VS Code 상태 응답** — 스프레드가 항상 덮어써 죽어 있던
    `"status": "ok"` 리터럴 제거(와이어 값 불변 — 응답은 확장이 보고한
    상태 그대로).
11. **워크스페이스 미도달 404** — 권한 검사가 조회에 선행해 영원히 닿지
    않던 404 arm 2개를 제거하고, "미지 id는 존재 노출을 피해 403"이라는
    반열거(anti-enumeration) 설계를 주석으로 명시.

## 죽은 코드 제거 (전부 증명 후 삭제)

- `tool_registry._wa()` — 호출처 0곳 (AST 스캔 + 47개 거버넌스 항목 전수,
  제거 전후 정책 테이블 바이트 동일).
- `workspace_os_utils._snapshot_graph_import_payload` — 참조 0곳.
- 워크스페이스 404 arm 2개 (위 11번).
- vLLM 침묵 성공 재확인 분기 (위 5번).
- `model_catalog._model_family_version`의 빈 버전 가드 — 정규식 캡처가
  비는 코드포인트가 존재하지 않음을 전 유니코드 스캔(0x110000)으로 증명.
- `model_compat`의 `if mlx_lm_available:` — 상위 가드가 이미 반환해 항상
  참임을 증명 후 조건 제거(라인 수 불변).
- `retrieval_docgen`의 `if query:` — 상단 blank-query 반환이 선행해 증명된
  죽은 조건.

## 분기 커버리지 — 어떻게 100%가 됐나

- 기준선 99.22%(미달 346아크)에서 시작 — 라인 100% 작업이 이미 대부분의
  분기를 몰아붙인 상태였습니다.
- 347아크(+클로저 3아크)를 서브시스템별 6개 패키지로 나눠 마감: 루프 0회
  진입, if-False, 예외-미발생, 동시성 레이스(더블체크 락의 지는 쪽,
  로드 중 리바인딩) 같은 "실행된 적 없는 방향"들을 결정적 시임으로
  구동했습니다.
- linux 컨테이너 검증이 또 한 번 **개발 머신 홈 데이터로만 커버되던
  3아크**를 드러냈고(두 번째 가드너의 INDEX 존재 분기, manual 트리거
  루프백, 무변경 마이그레이션 반환), 전부 밀폐 테스트로 교체했습니다.
- `pragma: no branch`는 정확히 2줄: `runtime/contracts.py:284`(같은 줄에
  def와 본문이 있어 CPython이 해당 아크를 방출하지 않음을 실측),
  `api/voice_capture.py:71`(예외 경로 위라 거짓 방향이 뒤 라인에 도달
  불가). 각 줄에 사유가 있습니다.

## 검증

| gate | result |
| --- | --- |
| pytest (full, line+branch 게이트) | 5,798 passed · **100.00%** — 3회 연속 재현 |
| linux python:3.14 컨테이너 | 5,798 passed · 100.00% (게이트 통과) |
| fresh python 3.11 venv (fastapi 0.141) | 5,792 passed (unit) |
| vitest (coverage) | 1,646 passed · 100% 4지표 (불변) |
| mypy | 276 / 276 modules, 0 errors |
| ruff | All checks passed |

## 새로 기록된 관찰 (이번에 수정 안 함 — 다음 후보)

- `kgv2_edges` 뷰의 `COALESCE(legacy_type, type)`: 네이티브 canonical
  쓰기는 `legacy_type = ''`(NULL 아님)로 저장하므로, canonical 엣지가 v2
  기본 읽기 경로에서 `type = ''`로 읽힙니다(legacy 테이블에는 올바른
  라벨). (lattice_brain/graph/projection.py:64-70)
- `automation_intelligence`의 recurring-question 저신뢰 억제 게이트는 출하
  상수로는 발화할 수 없는 죽은 정책입니다(신뢰 하한 0.475 > 임계 0.35).
- 일부 기존 테스트가 `HOME` 아래 실제 `~/.ltcai-brain`/`~/.ltcai`를
  생성합니다 — 이번 mac-vs-linux 패리티 갭들의 공통 원인이었고, 시임
  주입으로의 정리가 다음 위생 후보입니다.

## Artifacts (exact filenames)

- `dist/ltcai-11.0.1-py3-none-any.whl`
- `dist/ltcai-11.0.1.tar.gz`
- `ltcai-11.0.1.tgz`
- `dist/ltcai-11.0.1.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.0.1_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다. 패키지 스토어 배포는 owner-run입니다.
