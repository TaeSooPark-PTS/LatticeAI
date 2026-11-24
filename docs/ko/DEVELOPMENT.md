[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../DEVELOPMENT.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./DEVELOPMENT.md)

# Lattice AI 개발 가이드

> **상태: 정본 (14.0.0)** — 현재 릴리스와 동기화되는 기여자 안내 문서입니다.

현재 릴리스: **14.0.0 — Living Synthesis**.

이 문서는 기여자 온보딩 경로를 정리합니다: 저장소를 어떻게 실행하는지, 변경 사항이 어디에 들어가야 하는지, 어떤 게이트가 실패하는지, 그리고 제품의 정직성을 지키는 내부 규칙을 다룹니다. 제품 포지셔닝과 5분 첫 실행 흐름은 [README.md](../../README.md)와 [ONBOARDING.md](./ONBOARDING.md)에 있습니다. 지원되는 릴리스 이력(12.0.0 이상)은 [CHANGELOG.md](../CHANGELOG.md)와 [RELEASE.md](../../RELEASE.md)에 있으며(`12.x`는 현대적인 Rust 호스트 + 도메인 그룹 아키텍처 세대이므로 [SECURITY.md](../../SECURITY.md)도 `12.x`만 지원합니다), 아직 열린 과제는 [ROADMAP.md](./ROADMAP.md)(참고 문서, 정본 아님)에 정리되어 있습니다.

## 제품 계약

Lattice AI는 어떤 AI 모델로 교체하더라도 사용자의 지식을 영속적으로 유지하는 로컬 우선 디지털 브레인입니다.

모든 엔지니어링 작업은 다음 경계를 지켜야 합니다.

- 영속 자산은 Brain이며, 모델은 교체 가능한 보이스입니다.
- SQLite가 라이브 로컬 Brain 저장소입니다.
- Docker, 클라우드 모델, 다운로드, 업데이트 확인, Brain Network는 모두 옵트인입니다(PostgreSQL 스케일/마이그레이션 도구와 Telegram 브리지는 11.6.0에서 AI 워커로 전환된 플랫폼 코드와 함께 제거되었습니다).
- 임포트 전용 경로는 MLX/GPU 초기화, 파일 쓰기, 네트워크 호출을 수행해서는 안 됩니다.
- 일반 Brain 사용 화면은 관리자/운영자 제어 화면과 분리되어 있어야 합니다.

## 10분 빠른 시작

Python 3.11+, Node, Rust 툴체인(`cargo`)이 필요합니다. 제품 본체는 `lattice-host`이며, Python은 호스트가 관리하는 20개 라우트의 연산 워커입니다.

```bash
git clone <this-repo>
cd "Lattice AI"          # 또는 클론한 디렉터리 이름

python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"

npm ci
npm start                # 제품 실행. PATH에 구버전 전역 LTCAI가 있다면 node bin/ltcai.js 사용
```

그다음 아래 주소를 엽니다.

```text
http://127.0.0.1:4825/app
```

첫 화면은 깨우기(wake) 단계입니다: 언어 전환기(한국어 / English), Living Brain, 한국어 헤드라인 **「내 지식이 살아나는 Brain을 시작하세요.」** 와 기본 버튼 **「Brain 지금 깨우기」** 가 표시됩니다. 기존 로컬 Brain이 있다면 바로 홈 캔버스로 건너뛸 수 있습니다.

Apple Silicon 로컬 모델 확장(MLX)은 실제로 모델을 로드할 때만 설치합니다.

```bash
.venv/bin/pip install -e ".[local]"
```

### `npm start` vs `npm run dev`

| 명령어 | 실제 동작 | 결과 |
| --- | --- | --- |
| `npm start` | `LTCAI` 바이너리 → [`bin/ltcai.js`](../../bin/ltcai.js) | **제품 본체**: `127.0.0.1:4825`에서 `lattice-host`가 워커를 관리하고 `/app`에서 SPA를 서빙 |
| `node bin/ltcai.js` | 동일한 바이너리를 직접 호출 | 동일한 제품 본체; `PATH`에 오래된 전역 `LTCAI`가 있을 때 사용 |
| `npm run dev` | `node scripts/run_python.mjs -m latticeai.cli.entrypoint --reload` | **제품 본체가 아님**. uvicorn `--reload`로 20개 라우트 워커만 띄움(`Lattice AI worker on http://127.0.0.1:4825` 출력). SPA 없음. |

`scripts/run_python.mjs`는 `.venv/bin/python`을 우선합니다. `bin/ltcai.js`는 `LTCAI_PYTHON`, 관리형 `~/.ltcai/npm-python`, `.venv` 순서로 찾습니다. 둘 다 있을 때는 `LTCAI_PYTHON`으로 워커 인터프리터를 고정하세요.

`bin/ltcai.js`는 `LATTICEAI_HOST_BIN` / `LTCAI_HOST`, `rust/target/release/lattice-host`, `rust/target/debug/lattice-host` 순서로 호스트 바이너리를 찾습니다. 아무것도 없으면 `cargo build -p lattice-host`(디버그)를 실행합니다. 오래된 `rust/target/release/lattice-host`가 최신 디버그 바이너리보다 우선하므로 `-V`가 13.4.0이 아니라면 경로를 지정하거나 `cargo build -p lattice-host --release`로 다시 빌드하세요.

`npm start`와 `npm run dev`를 같은 포트에서 동시에 실행하지 마세요. 워커 진입점은 기본적으로 `LATTICEAI_PORT` 또는 **4825**를 사용합니다. 워커 연산 로직을 반복 개발할 때는 `LATTICEAI_PORT=4835 npm run dev`를 사용하세요(호스트와 짝지을 때는 `--no-spawn`과 워커 포트 지정 사용).

## 코드 지도 — 어디에 무엇을

두 개의 크레이트별 아키텍처 맵이 계약입니다. 아래 표는 색인이며, 파일을 추가하기 전에 해당 그룹의 `mod.rs`를 먼저 읽으세요.

- [`rust/lattice-agent/ARCHITECTURE.md`](../../rust/lattice-agent/ARCHITECTURE.md) — `kernel/`이 결정합니다. `parse/`·`content/`·`tools/`는 커널을 임포트하지 않으며, `surface/`는 HTTP를, `prompts/`는 프롬프트 템플릿을 맡습니다. 의존성 화살표는 아래로만 향합니다.
- [`rust/lattice-platform/ARCHITECTURE.md`](../../rust/lattice-platform/ARCHITECTURE.md) — 이 크레이트는 **기능을 제공**할 뿐, 실행 허용 여부(`lattice-agent` 커널)를 결정하거나 사실 데이터(`lattice-core`)를 소유하지 않습니다.

```text
lattice-host            단일 진입점 — 모든 라우트 패밀리 마운트
  lattice-platform      제품이 제공하는 기능 표면
      workspaceos/      공간, 사용자, 다이얼, workspace_os.json
      toolsurface/      MCP, 도구, 플러그인, 에이전트, computer-use
      governance/       검토, 제안, 자동화, 워크플로우, 훅
      knowledge/        내보내기/가져오기/백업, 네트워크, 경계, 음성
      modelops/         카탈로그, 추천, 초기 설정
      adminops/         감사 로그, 보안 대시보드, 퍼널
      shell/            SPA + 308 리다이렉트
  lattice-agent         실행 가능 여부 결정 및 에이전트 실행
  lattice-core          사실 저장소 — GraphWriter가 유일한 그래프 라이터
  lattice-retrieval     검색, 기억, 연대기, 커맨드, 근거
  lattice-ingest        파일, 폴더, 브라우저 수집
  lattice-jobs          임베딩 드레인 + 인덱스 API
  lattice-chat          POST /chat
  lattice-auth          사용자 인증 및 권한 정책
Python worker           연산 전용 — 20개 허용 목록 라우트
frontend/src            SPA
```

각 크레이트의 `src/lib.rs`에 있는 호환성 재노출(re-export)은 외부 소비자를 위해 v12 이전 경로를 유지합니다. 크레이트 내부의 새 코드는 실제 경로(`crate::kernel::state`, `crate::adminops::admin`)를 사용하세요.

| 할 일 | 어디에 | 하지 말 것 |
| --- | --- | --- |
| **새 제품 API 라우트** | 주제를 나타내는 `lattice-platform` 도메인(`workspaceos/`, `toolsurface/`, `governance/`, `knowledge/`, `modelops/`, `adminops/`). 한 모듈이 한 패밀리를 담당(`MOUNTED: &[(&str, &str)]` + `router(state)`)하고 [`rust/lattice-host/src/gateway/product.rs`](../../rust/lattice-host/src/gateway/product.rs)에 마운트합니다. **페이지 셸**은 `shell/ui_redirects`의 308 리다이렉트로 처리합니다(`require_user`가 필요한 `GET /plugins/sdk`는 `toolsurface::plugins` 예외). | 검색/연대기를 platform에 두지 마세요(`lattice-retrieval`). 수집은 `lattice-ingest`, 채팅은 `lattice-chat`, 인덱스 드레인은 `lattice-jobs`, 인증은 `lattice-auth`, 에이전트 루프 HTTP는 `lattice-agent` `surface/`입니다. 핸들러가 허용 여부를 독자적으로 재판단하면 `lattice_agent::kernel::permission`을 포크한 것이고, 그래프를 직접 열면 제2의 라이터가 됩니다. |
| **에이전트 툴 추가** | 에이전트 턴 내부에서 도는 도구는 `lattice-agent` `tools/`에서 대상에 맞는 파일(`files`, `vault`, `shell`, `desktop`, `render`, `scaffold`, `local`)에 둡니다. 이름을 `tools::host::MUTATING_TOOLS` 또는 `RENDER_TOOLS`에 추가하고 [`tools/host.rs`](../../rust/lattice-agent/src/tools/host.rs) 디스패처에 분기를 추가하세요. 모든 경로는 `tools::sandbox::Workspace::resolve`로 해석하고 모든 쓰기는 `content::sanitize::sanitize_write_content`를 통과합니다. 기존 기능의 HTTP 노출은 `lattice-platform` `toolsurface/tools/` + `tools::governance_for`로 합니다. | 핸들러 안에 별도 권한 검사를 넣지 마세요(커널이 이미 승인함). 원시 경로에 `std::fs`를 직접 쓰지 마세요. 읽기 전용 연산 핸들러나 포인터 도구(`computer_*`)는 의도적으로 워커(`POST /agent/tool`)에 둡니다. |
| **파싱 보정 추가** | `lattice-agent` `parse/action.rs`(또는 새 모델 프레임이면 `parse/channel.rs`). 가장 단순하고 리터럴에 가까운 순서대로 체인 끝에 추가하고 **반환되는 `repairs` 목록에 이름을 기록**하세요. CPython 기본 동작(`pyjson`, `pyliteral`, `pyshlex`, `pystr`)은 [`rust/fixtures/agent/`](../../rust/fixtures/agent/FROZEN.md)에 고정되어 있습니다. Python에 없던 새 응답은 **새 키** 아래에 둡니다([`agent_loop/FROZEN.md`](../../rust/fixtures/agent_loop/FROZEN.md)의 `*_extended` 참고). | 조용한 보정은 버그입니다. 모델이 쓰지 않은 도구 이름·경로·인자를 만들어내지 마세요. 의사결정(`kernel/`)이나 파일 본문 복구(`content/`)를 여기에 두지 말고, 동결된 픽스처 행을 제자리에서 수정하지 마세요. |
| **새 워커 연산** | 임베딩, 파싱, 추출, 렌더링, ASR, LLM 등 순수 연산만 담당합니다. `latticeai/api/worker_compute.py` 또는 [`latticeai/runtime/build_phases/worker_profile.py`](../../latticeai/runtime/build_phases/worker_profile.py)의 `WORKER_ROUTES`/`WORKER_SEAM_ROUTES`에 `(method, path)`를 추가한 뒤 `.venv/bin/python scripts/gen_worker_allowlist_fixture.py`로 커밋된 프로젝션을 재생성하세요. `tests/unit/test_worker_allowlist.py`가 이를 고정하고 `lattice-host`가 [`rust/fixtures/worker_allowlist.json`](../../rust/fixtures/worker_allowlist.json)을 `include_str!`로 포함합니다. 라우트는 총 **20개**이며, 네이티브도 아니고 목록에도 없으면 `404`입니다. | 제품 기능을 워커 라우트로 추가하지 마세요. 워커는 그래프·세션·파일 등 어떤 상태도 저장하지 않습니다. `latticeai.app_factory.build_context`는 제품이 아니라 **워커** 조립 루트이며 임포트 시 MLX/GPU 초기화·싱글톤 생성·파일 쓰기·네트워크 호출이 없어야 합니다. |
| **프론트 화면 / 패널** | 라우트 화면은 `frontend/src/pages/`(`Brain`, `Capture`, `Chronicle`, `Act`, `Library`, `System`), 기능 패널은 `frontend/src/features/<area>/`, 공통 크롬은 `frontend/src/components/`, 해시 라우트는 `frontend/src/routes.ts`에 둡니다. 문구는 `frontend/src/i18n/`에서 `registerCopy`로 등록하며 한국어를 먼저 작성하고 영어 쌍을 같은 모듈에 둡니다(`t()`는 `COPY.ko`로 폴백). 색상은 [`frontend/src/styles/tokens.css`](../../frontend/src/styles/tokens.css)의 `hsl(var(--token))`만 사용하고, 실패한 `ApiResult`는 항상 오류/사용 불가 상태로 표시합니다. Vitest 테스트를 함께 배치하며 커버리지 하한은 `all: true` 기준 100%입니다. | TSX에 사용자 노출 한글/영문 리터럴을 직접 쓰지 마세요(`scripts/check_i18n_literals.mjs`). 등록하지 않은 i18n 네임스페이스를 임포트하지 마세요(`check:i18n-namespaces`). 기본 첫 화면에 런타임 지표나 관리자 도구를 넣지 마세요. `frontend/src/api/openapi.ts`는 손으로 편집하지 말고 `npm run frontend:openapi`로 재생성하세요. |
| **지식 그래프 쓰기** | 오직 [`lattice_core::graph_write::GraphWriter`](../../rust/lattice-core/src/graph_write/mod.rs)만 사용합니다. 프로덕션 `open()`은 워크스페이스가 생략된 쓰기에 `"personal"`을 자동 기록합니다. 청크, 개념, 트리플, 임베딩은 요청 데이터로 전달되며 라이터는 절대 모델을 호출하지 않습니다. | 다른 크레이트에서 `knowledge_graph.sqlite`를 쓰기 모드로 열지 마세요. 은퇴한 `/worker/graph/mutate`를 쓰지 마세요. `with_parts`는 동결 골든 패리티 전용이며 제품 코드용이 아닙니다. |
| **검증 / 골든** | 각 픽스처 패밀리의 `FROZEN.md` 규칙을 따릅니다: **Agent kernel**([`rust/fixtures/agent/`](../../rust/fixtures/agent/FROZEN.md)), **Agent loop**([`rust/fixtures/agent_loop/`](../../rust/fixtures/agent_loop/FROZEN.md)), **Retrieval / embeddings**([`rust/fixtures/golden/`](../../rust/fixtures/golden/FROZEN.md)), **Graph writes**([`rust/fixtures/graph_write/`](../../rust/fixtures/graph_write/FROZEN.md)), **Chunking**([`rust/fixtures/chunking/`](../../rust/fixtures/chunking/FROZEN.md)), **HTTP replay**([`rust/fixtures/http/`](../../rust/fixtures/http/FROZEN.md)). Python 워커는 `npm run test:unit`, SPA는 `npm run test:frontend`, 비주얼은 `npm run test:visual`, 통합 테스트는 `npm run test:integration`으로 실행합니다. | 동결된 기대값은 불가침입니다. 이식 코드가 어긋나면 이식 코드를 고치세요. 의도된 신규 동작은 기존 행을 덮어쓰지 말고 **새 키**나 새 파일에 기록하세요. |

새 게이트나 판정은 `lattice-agent` `kernel/`에 들어가며 **fail-closed**로 동작해야 합니다: 알 수 없는 경우는 거부, 검증 불가 시 `NEEDS_REVIEW`, 정책 없는 도구는 가능한 가장 위험한 등급으로 분류합니다.

새 내장 프롬프트는 `prompts/mod.rs`에 상수로 추가하고 실제 파서를 통과시키는 테스트를 함께 작성합니다. 호출자가 제공한 프롬프트가 항상 우선합니다.

`workspace_os.json`의 라이터는 `workspaceos::workspace::store::WorkspaceOsStore` 하나뿐이며, 감사 로그는 오직 `adminops::admin::append_audit_event`를 통해서만 기록합니다.

## 검증 게이트

작업 중에는 영향 범위가 가장 작은 게이트부터 실행하고, 넓은 범위의 런타임·API·UI·릴리스 커밋 전에는 아래 전체를 통과시키세요.

```bash
npm run lint
npm run typecheck
npm run test:frontend
npm run test:unit
npm run docs:check-links
npm run rust:lint          # fmt --check + clippy -D warnings
npm run rust:test
```

해당 표면을 수정했을 때 추가로 실행하는 명령어:

```bash
npm run test:integration
npm run test:visual
npm run test:coverage
npm run test:frontend:coverage
npm run desktop:tauri:check
npm run release:evidence
npm run release:artifacts
npm run release:validate
```

### `npm run lint` — 10개 항목 실행 순서

1. `lint:python` — `ruff check .` 후 `mypy`.
2. `lint:visual` — 모든 `tests/visual/*.js`, `*.cjs`, `tests/visual/mock_server/*.cjs`에 `node --check`.
3. `lint:frontend` — `scripts/lint_frontend.mjs`(`tsc --noEmit` + 정적 검사).
4. `frontend:openapi:check` — `frontend/openapi.json`과 `frontend/src/api/openapi.ts`의 동기화 확인(`npm run frontend:openapi`로 재생성).
5. `scripts/check_i18n_literals.mjs` — `frontend/src` TS/TSX 내 사용자 노출 리터럴 금지.
6. `check:i18n-namespaces` — 지연 로드 청크는 자신이 임포트한 i18n 네임스페이스 키만 참조 가능.
7. `check:bundle` — 초기 JS gzip 번들 150 KiB 미만 유지(`React.lazy` 활용).
8. `check:server-i18n` — `scripts/check_server_i18n.mjs` 대상 워커 라우터 6개 모두 `http_error` / `translate` 사용 강제.
9. `scripts/check_release_evidence_bound.mjs` — 릴리스 증거 바인딩 검사.
10. `check:max-file-lines` — 파일당 최대 1,000줄 제한 검사.

11.8.0에서 로컬 체인에서 제거된 세 항목(`check:python`, `check:legacy-debt`, 로컬 확장 테스트)은 CI에서 직접 처리되므로 다시 추가하지 않습니다. 자세한 내용은 [CI_AND_RELEASE_GATES.md](../CI_AND_RELEASE_GATES.md)를 참고하세요.

### 커버리지 하한

Python(`[tool.coverage.report] fail_under = 90`, `pyproject.toml`)은 **라인 커버리지** 하한입니다(`npm run test:coverage`). 프론트엔드(`vitest.config.mts`)는 `all: true`로 **구문·분기·함수·라인 모두 100%**를 요구합니다(`npm run test:frontend:coverage`). `frontend/src/i18n/**`, `frontend/src/api/openapi.ts`, `frontend/src/main.tsx`는 의도적으로 제외됩니다.

### 최대 파일 줄 수 (1,000줄)

`scripts/check_max_file_lines.mjs`는 테스트를 포함해 git으로 추적되는 모든 `*.py`, `*.ts`, `*.tsx`, `*.js`, `*.mjs`, `*.cjs`, `*.css`, `*.rs` 파일이 1,000줄을 넘으면 실패합니다. 응집도 기준으로 모듈을 나누세요. 생성 산출물(`frontend/src/api/openapi.ts`, `static/app/`, `static/vendor/`, `src-tauri/gen/`)만 예외입니다.

### Clippy `-D warnings` 및 병렬 `cargo nextest`

`npm run rust:lint`는 `rust/`에서 `cargo fmt --all -- --check`와 `cargo clippy --workspace --all-targets -- -D warnings`를 실행합니다. `npm run rust:test`(`scripts/run_rust_tests.mjs`)는 `cargo-nextest`가 설치되어 있으면 병렬 실행(+ doctests)하고, 없으면 `cargo test --workspace`로 폴백합니다.

### 문서 게이트

`npm run docs:check-links`는 `scripts/check_markdown_links.mjs`와 `scripts/check_doc_status.mjs`(루트 정본 문서 및 `docs/**`의 모든 상대 링크 검증)를 실행합니다. 정본 문서는 `package.json`과 일치하는 현재 릴리스 버전을 유지해야 하며, `npm run docs:check-current`([`scripts/check_current_release_docs.mjs`](../../scripts/check_current_release_docs.mjs))가 릴리스 대상 문서 집합을 고정합니다. 참고 문서([ROADMAP.md](./ROADMAP.md) 등)와 과거 변경 이력은 고정 대상이 아닙니다.

### 릴리스 증거 바인딩

`scripts/check_release_evidence_bound.mjs`는 `output/release/v13.4.0/SCREENSHOT_INDEX.md`가 현재 `static/app/asset-manifest.json` 다이제스트와 비주얼 목 서버 지문과 일치하는지 확인합니다. 수정하려면 `npm run build:assets` 후 `npm run release:evidence`를 실행하세요.

## 내부 원칙

- **정직성**: 스텁은 스텁으로 표시합니다. 완료할 수 없는 기능은 가짜 성공이나 점수 대신 `unavailable` / `skipped` / `simulation` / `awaiting_approval`과 사유를 반환합니다. [FEATURE_STATUS.md](../../FEATURE_STATUS.md)와 [SURFACE_PARITY.md](./SURFACE_PARITY.md)의 `—`는 설계 경계와 이유를 명시합니다. [PROJECT_PRINCIPLES.md](../../PROJECT_PRINCIPLES.md) 참고.
- **fail-closed 검증**: 검증 불가능한 에이전트 결과는 `NEEDS_REVIEW`, 알 수 없는 권한 사례는 거부, 기준 SHA가 달라진 변경 제안 승인은 병합이나 덮어쓰기 없이 **409**입니다.
- **한국어 UX 우선**: 제품 문구는 한국어로 먼저 작성하고 같은 `registerCopy` 테이블에 영어를 함께 넣습니다(`t()`는 `COPY.ko` 폴백).
- **토큰 전용 CSS**: 색상은 `frontend/src/styles/tokens.css`의 `hsl(var(--token))`만 사용합니다.
- **워크스페이스 `null` = personal**: `DEFAULT_WORKSPACE_ID`는 `"personal"`이며, 헤더와 쿼리/본문의 워크스페이스가 충돌하면 403을 반환합니다.

## 문서 동기화

사용자 노출·API·런타임·릴리스·패키징 변경 시 다음 문서를 점검하세요.

- [README.md](../../README.md)
- [ARCHITECTURE.md](../../ARCHITECTURE.md)
- [FEATURE_STATUS.md](../../FEATURE_STATUS.md)
- [RELEASE.md](../../RELEASE.md)
- [CHANGELOG.md](../CHANGELOG.md)
- 보안/신뢰 변경 시 [SECURITY.md](../../SECURITY.md)
- 에디터 확장 변경 시 [vscode-extension/README.md](../../vscode-extension/README.md)
- 크레이트 또는 서비스 경계 변경 시 [ARCHITECTURE.md](./ARCHITECTURE.md)
- 기여자 경로·코드 지도 변경 시 이 문서
- 열린 갭이 닫히거나 새 항목이 생겼을 때 [ROADMAP.md](./ROADMAP.md)

릴리스·배포 예제는 항상 정확한 대상 버전 파일명을 사용해야 하며 와일드카드(`dist/*`) 업로드 명령을 문서화하지 마세요.

13.4.0 릴리스 작업의 정확한 아티팩트 파일명은 다음과 같습니다.

- `dist/ltcai-14.0.0-py3-none-any.whl`
- `dist/ltcai-14.0.0.tar.gz`
- `ltcai-14.0.0.tgz`
- `dist/ltcai-14.0.0.vsix`
- `src-tauri/target/release/bundle/dmg/Lattice AI_14.0.0_aarch64.dmg`

DMG는 ad-hoc 서명(실질적으로 미서명) 상태이며, `npm run release:validate`는 Developer ID 서명이 아니라 파일명과 존재 여부를 검사합니다.
