# Lattice AI v11.3.0 — Time Remembers (2026-08-11)

> **Status: historical** — point-in-time release note.

11.1.0이 두뇌에 시간 감각(temporal 컬럼과 `as_of` 읽기)을 심었지만, 그것을
볼 수 있는 화면은 없었습니다. 11.3.0은 그 감각을 **기억의 연대기**라는
7번째 주 화면으로 꺼내고, 동시에 코드베이스에서 **1,000줄이 넘는 파일을
전부 없앴습니다** — 동작 변화 0을 증명하면서.

설계 문서: [docs/v11.3.0_PLAN.md](../v11.3.0_PLAN.md)

## 기억의 연대기 (Brain Chronicle)

새 주 화면 **연대기** (`#/chronicle`, 별칭 `#/timeline`) — everyday 내비
네 번째 항목(대화 · 자료 · 기억 · 연대기).

- **성장 곡선 + 시간 핸들** — 누적 기억(자료·개념·연결·대화)의 SVG 영역
  차트. 핸들을 끌거나(터치 지원) 방향키로 하루씩, Home/End로 처음/끝
  (ARIA 슬라이더). 핸들이 과거에 있으면 화면 전체가 그 시점을 따라갑니다.
- **활동 히트맵** — 주×요일 격자로 하루의 활동 밀도. 셀은 버튼이고,
  클릭하면 그 날로 이동합니다.
- **그날의 이야기** — 선택한 날짜에 두뇌에 들어온 것들을 평이한 한국어
  카드로: 자료(제목·출처), 새로 생긴 개념, 나눈 대화(첫 줄 미리보기),
  달라진 사실(대체·종료된 사실). 각 카드는 기억 검색(`#/hybrid-search`),
  그래프(`#/knowledge-graph`), 대화(`#/brain`)로 이어집니다.
- **그때의 두뇌 (되감기)** — 과거 시점의 노드·연결 수와 그때 중요했던
  개념 목록. `store.as_of()`의 첫 사용자 노출입니다.
- **정직한 빈 상태** — 새 두뇌에는 "오늘부터 기록이 쌓입니다"라고만
  말합니다. 요약 생성도 모델 호출도 없습니다 — 전부 이미 저장된 사실의
  재배열입니다.

### API (전부 읽기 전용, 스키마 변경 0, 쓰기 0)

- `GET /api/chronicle/overview` — 앱 시간대 기준 일 단위 버킷 합계 +
  희소 활동 시리즈(활동 있는 날만).
- `GET /api/chronicle/day/{date}` — 그날의 이야기. 그룹 목록은 200개
  상한, `counts`는 항상 참값(상한을 넘을 수 있음).
- `GET /api/chronicle/as-of?ts=…` — 그 시점의 그래프 슬라이스 통계 +
  중요 개념(최대 12). `as_of` 슬라이스는 기존 temporal 읽기 API를 통해서만
  계산합니다(중복 구현 없음).

원천: `ingestion_provenance` · `nodes_v2`/`edges_v2`(temporal 3열) ·
`conversation_messages` · `edge_occurrences`. 인증·게이트·워크스페이스
경계는 커맨드 센터와 동일 규약. 서버 메시지는 ko/en 등록(19번째 라우터).

## No Big Files — 전면 분해

"누가 봐도 큰 파일"이 리포에 남지 않게 했습니다. 1,000줄 초과 1st-party
파일 **28개를 전부** 응집 단위로 분해했습니다.

- `frontend/src/styles.css` **10,956줄 → 48줄** 엔트리 + core/ 20개 파일.
  분해 전후 **vite 빌드 산출 CSS 번들이 byte-identical** (cmp + sha256,
  warm/cold 재현) — 화면이 한 픽셀도 달라질 수 없음을 빌드로 증명.
- i18n `brain.ts`/`workspace.ts` → 도메인 파트 14개 + 얇은 애그리게이터.
  병합 결과 key→value 맵 **완전 동일 증명**(ko/en × 2 네임스페이스).
- Python 18개 모듈 → 동명 패키지 (agent · build_phases · telegram_bot ·
  wizard · model_runtime · embedding_providers · file_generation ·
  memory_service · brain_intelligence · models/router · ingestion ·
  portability · multimodal · _kg_common · retrieval · retrieval_vector ·
  discovery_index · projection). 모든 분해에 **AST 동등성 증명** —
  원본의 모든 top-level 함수/클래스(분할 클래스는 멤버 단위)가 새 패키지에
  정확히 한 번, 동일한 `ast.dump`로 존재. import 경로는 하나도 깨지지
  않았고, 테스트의 monkeypatch 대상 317+개 사이트는 실제 위치로 재표적.
- VS Code 확장 `extension.ts` 1,104줄 → 7개 모듈. 대형 테스트 5개와
  visual mock 서버도 같은 기준으로 분해.
- **재발 방지 게이트**: `scripts/check_max_file_lines.mjs`가 `npm run
  lint`에서 추적 파일 전체(1,319개)를 검사 — 1,000줄 초과는 CI 실패.
  생성물(openapi.ts, static/app, vendor)만 사유 명시 제외.

## 게이트가 이를 되찾음

- **릴리스 증거 결속**이 mock 서버 전체 트리(엔트리 + 라우트 모듈 9개)를
  지문화합니다 — 분해로 페이로드가 하위 모듈로 이동하며 생긴 사각을 제거
  (`scripts/lib/mock_server_fingerprint.mjs`, 읽기/쓰기가 한 코드 경로).
- **픽셀 게이트**가 "이번 릴리스에 새로 생긴 화면"(베이스라인 없음)을
  이해합니다 — 클레임되지 않은 신규 화면은 보고만 하고, 클레임된 화면이
  베이스라인에 없으면 여전히 큰 소리로 실패합니다. `13-chronicle.png`가
  첫 사례입니다.
- **i18n 고아 키 스캔**이 네임스페이스 파트 파일 구조를 이해합니다.

## 검증

- Python **6,560 테스트 · 40,488문 · 11,052분기 100.00%**
  (`fail_under = 100`, 문+분기), macOS 3.14 + fresh-resolve 3.11.
- 프론트 **1,761 테스트 · 문/분기/함수/라인 100%**, Playwright 40/40
  (신규 연대기 스펙 5/5 포함, 실브라우저 다크모드·390px 모바일 검증).
- mypy 400+ 파일 0 에러 · ruff 클린 · 번들 예산 유지(초기 103.4KiB gzip,
  연대기는 lazy chunk 22.3KiB).

## 산출물

- `dist/ltcai-11.3.0-py3-none-any.whl`
- `dist/ltcai-11.3.0.tar.gz`
- `ltcai-11.3.0.tgz`
- `dist/ltcai-11.3.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_11.3.0_aarch64.dmg`

와일드카드 업로드는 사용하지 않습니다.
