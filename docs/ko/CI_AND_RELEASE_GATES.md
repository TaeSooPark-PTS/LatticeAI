[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../CI_AND_RELEASE_GATES.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./CI_AND_RELEASE_GATES.md)

# CI 및 릴리스 게이트

> **상태: 정본 (2026-11-25 갱신) — 현재 릴리스: 14.1.0 — Living Synthesis**

`main` 브랜치와 릴리스를 보호하는 GitHub Actions 워크플로우, 공급망 하드닝(액션 SHA 고정), CI 통과 강제형 자동 CD 파이프라인, 권장 브랜치 보호 설정을 정리한 문서입니다. 관련 문서: [`SECURITY_AUDIT.md`](../SECURITY_AUDIT.md), [`BENCHMARKS.md`](../BENCHMARKS.md).

## 워크플로우 개요

| 워크플로우 | 파일 | 트리거 | 역할 |
| --- | --- | --- | --- |
| CI | `.github/workflows/ci.yml` | `main` 푸시 / PR | 10개 병렬 게이트: Frontend/i18n/문서/언어 분리/확장/Vitest 100%, Python 3.11/3.12/3.14 Ubuntu + 3.11 macOS 매트릭스, 통합 스모크, 패키지 빌드·휠 스모크, Ubuntu 2분할 Rust `nextest`(`rust-core` + `rust-runtime`), Windows Rust `nextest`(`rust-windows`), macOS Tauri 테스트(`tauri-check`), 다이제스트 고정 Docker 스모크(`docker-smoke`), PR + `main` Playwright 시각 스모크(`visual`) |
| Release | `.github/workflows/release.yml` | `workflow_run` (`main`에서 `CI` 완료), 태그 `v*` / `[0-9]*`, 수동 실행 | 정확한 커밋 SHA의 `CI` 통과를 하드 강제(fail-closed), `[skip release]` 제외 지원, 전체 매니페스트 버전 일치(`bump_version.py --check`) 및 문서·언어 게이트 확인 후 미배포 버전 자동 태깅, 핵심 아티팩트 + Apple Silicon macOS DMG 빌드/서명/검증, GitHub Release 게시 후 `workflow_call`로 CD 자동 연결 |
| CD | `.github/workflows/publish.yml` | `workflow_call`, GitHub Release `published`, 수동 실행 | 정확한 커밋 SHA의 `CI` 통과를 독립 재검증, GitHub Release에 이미 첨부된 아티팩트를 SHA-256 검증 후 재사용(중복 빌드 제거), npm / PyPI / VS Code Marketplace / Open VSX 멱등 배포, DMG가 이미 존재하면 macOS 러너 할당 생략 |
| Visual Smoke | `.github/workflows/visual.yml` | 야간 크론, 수동 실행 | Chromium 캐시 및 공유 라이브러리 패스트패스를 사용하는 예약/수동 Playwright 시각 스모크 (PR 및 `main` 푸시 검증은 `ci.yml`에서 실행) |
| Sidecar E2E | `.github/workflows/e2e-sidecar.yml` | 태그, 관련 PR 경로, 야간 크론, 수동 실행 | 15초 APT 미러 페일오버가 적용된 실제 사이드카 Playwright E2E 검증 |
| Nightly RAG Eval | `.github/workflows/nightly-rag-eval.yml` | 야간 크론, 수동 실행 | `datasets/rag_eval_v1.json` 대상 실모델 RAG 평가 및 주장 근거·인용 검증 게이트(엔드포인트 미설정 시 정직하게 `skipped`/`unavailable` 보고) |
| Dependency Audit | `.github/workflows/dependency-audit.yml` | 태그, 주간 크론, 수동 실행 | `pip-audit` + `npm audit` + CycloneDX SBOM 생성 |
| Postgres Integration | `.github/workflows/postgres-integration.yml` | 수동 실행 전용 | **스킵 / 예정** — 현재 운영 기본 저장소는 SQLite이며 PostgreSQL 라이브 마이그레이션은 예정 상태 |

모든 워크플로우는 `concurrency.group`을 `<workflow-file-stem>-${{ github.ref }}`로 설정하며 `main`을 제외한 진행 중 실행을 취소합니다(`release.yml`은 `cancel-in-progress: false`). `ci.yml`은 최상위 권한을 `permissions: contents: read`로 제한합니다.

## CI 잡 구조 (`ci.yml`)

### 1. 단일 실행 Frontend, 확장 및 문서 (`frontend`)
Python 매트릭스마다 반복 실행하지 않고 `ubuntu-latest`에서 한 번만 실행합니다:
- `npm run lint:visual`, `npm run lint:frontend`, `npm run typecheck`(TypeScript 7.0.2 + `"noUncheckedIndexedAccess": true`)
- `node scripts/check_i18n_literals.mjs`, `npm run check:i18n-namespaces`, `npm run check:bundle`, `npm run check:server-i18n`
- `node scripts/check_release_evidence_bound.mjs`, `npm run check:max-file-lines`
- `npm run docs:check-current`, `npm run docs:check-links`, `npm run docs:check-languages`
- `node scripts/check_ubuntu_runner_compat.mjs --diagnostic`
- `npm run test:browser-extension && npm run test:vscode-extension`
- `npm run test:frontend:coverage`(113개 테스트 파일 / 1,852개 테스트, 4개 지표 모두 100% 하한)

### 2. Python 매트릭스 (`test`)

| 레그 | OpenAPI 동기화 | Python 린트 (`ruff` + `mypy`) | 검색 베이스라인 (`hnswlib`) | 단위 테스트 | 커버리지 하한 (90% 이상) | 제품 준비 게이트 |
| --- | --- | --- | --- | --- | --- | --- |
| 3.11 + ubuntu-latest | 실행 | 실행(`BLE`, McCabe <= 20, Bandit `S608` + 최소 예외) | 실행 (`.[ann]`) | `pytest tests/unit/` | 미실행 | 실행 |
| 3.12 + ubuntu-latest | 미실행 | 미실행 | 미실행 | `pytest tests/unit/` | 미실행 | 미실행 |
| 3.14 + ubuntu-latest | 미실행 | 미실행 | 미실행 | `--cov` 실행에 통합 | 실행 (`fail_under = 90`) | 미실행 |
| 3.11 + macos-latest | 미실행 | 미실행 | 미실행 | `pytest tests/unit/` | 미실행 | 미실행 |

### 3. 병렬 후속 잡 (`integration` & `build`)
`frontend`와 `test`가 통과하면 두 잡이 동시에 실행됩니다:
- **`Integration smoke test` (`integration`)**: `lattice-host`가 Python 워커를 감독하는 실제 구성으로 `npm run test:integration`을 실행합니다.
- **`Build package` (`build`)**: wheel/sdist 빌드(`python -m build`), `twine check`, 격리 가상환경 휠 스모크(`scripts/wheel_smoke.py`)를 검증합니다.

### 4. Ubuntu 2분할 병렬 Rust 워크스페이스 (`rust-core` & `rust-runtime`)
두 잡 모두 `Swatinem/rust-cache`와 SHA 고정 `taiki-e/install-action`으로 설치한 **`cargo-nextest`**를 사용합니다:
- **`Rust (lint & core crates)` (`rust-core`)**: `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `lattice-core`·`lattice-retrieval`·`lattice-ingest`·`lattice-jobs`·`lattice-auth` nextest 및 doctest.
- **`Rust (agent, platform, chat & host)` (`rust-runtime`)**: `lattice-agent`·`lattice-platform`·`lattice-chat`·`lattice-host` nextest 및 doctest.

### 5. 크로스플랫폼 Rust, 데스크톱 및 컨테이너 잡 (`rust-windows`, `tauri-check`, `docker-smoke`)
- **`Rust (Windows workspace tests)` (`rust-windows`)**: `windows-latest`에서 `cargo nextest run --workspace --no-fail-fast`와 `cargo test --doc --workspace`를 실행해 Windows 핸들 고정 `safe_io.rs`와 경로 동작을 실증 검증합니다.
- **`Desktop shell (Tauri check & tests)` (`tauri-check`)**: `macos-latest`에서 `cargo test --manifest-path src-tauri/Cargo.toml`을 실행해 `PortPolicy::PinnedProduction`, 설정/권한 정합성, 게이트웨이 생명주기를 검증합니다.
- **`Docker image build smoke` (`docker-smoke`)**: SHA256 다이제스트로 고정된 멀티스테이지 `Dockerfile`을 빌드하고 `docker run --rm --entrypoint lattice-host latticeai:ci-smoke --version`을 스모크 검증합니다.

### 6. 고속 Playwright Visual Smoke (`visual`, PR 및 `main`)
- **PR과 `main` 푸시 모두**에서 실행(`ubuntu-24.04`)되어 머지 전에 시각 회귀를 차단합니다.
- `ldconfig -p`로 Chromium 필수 공유 라이브러리 8종을 먼저 확인해 `ubuntu-24.04` 러너에 이미 존재하면 `apt-get` 전체를 건너뜁니다.
- `actions/cache@v6.1.0`으로 `~/.cache/ms-playwright` 브라우저 캐시를 재사용합니다.

## 공급망 하드닝: 액션 SHA 고정

모든 워크플로우의 서드파티 `uses:`는 변경 불가능한 커밋 SHA에 100% 고정되어 있습니다:

| 액션 | 태그 / 참조 | 고정된 커밋 SHA |
| --- | --- | --- |
| `actions/checkout` | `v7.0.1` | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | `v7.1.0` | `6d4fd22b20465ccc0f7354476588e8df7c9ca0e0` |
| `actions/setup-node` | `v7.1.0` | `949feb2413d6458794dcd2491c4babbbce0c15c1` |
| `actions/cache` | `v6.1.0` | `55cc8345863c7cc4c66a329aec7e433d2d1c52a9` |
| `actions/upload-artifact` | `v7.0.2` | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
| `actions/download-artifact` | `v7.0.0` | `37930b1c2abaa49bbe596cd826c3c89aef350131` |
| `Swatinem/rust-cache` | `v2.9.3` | `d1527c8f67ef9af357290475d4af4b0fab8f12e0` |
| `dtolnay/rust-toolchain` | `stable` | `b725eda525362e7cf34af9a7243cac1e3f01a7ec` |
| `taiki-e/install-action` | `v2.90.8` | `fe3acb53a98b62c772db35de75ce0e4595e7ca8c` |

## CI 통과 강제 및 자동 CD 안전장치 (`release.yml` → `publish.yml`)

1. **실패 시 배포 완전 차단 (fail-closed):**
   - `release.yml`(`verify-ci`)과 `publish.yml`(`publish`) 모두 GitHub Actions API로 `git rev-parse HEAD` 커밋에 완료된 `ci.yml` `conclusion == "success"` 기록이 1건 이상 있는지 검증합니다.
   - CI가 실패했거나 실행되지 않은 커밋에서는 태그 푸시나 수동 실행으로도 배포할 수 없습니다.
2. **CI 통과 시 자동 릴리스 & 조기 버전 범프 방지:**
   - `main` 푸시의 `CI`가 성공하면 `release.yml`이 `workflow_run`으로 자동 트리거됩니다.
   - 커밋 메시지에 `[skip release]` 또는 `[no release]`가 포함되어 있으면 자동 태깅과 CD를 안전하게 건너뜁니다.
   - 이미 `origin`에 `v<version>` 태그가 있는 일반 커밋도 자동으로 스킵합니다.
   - 미배포 버전을 자동 태깅하기 전에 다음 3개 게이트를 모두 통과해야 합니다:
     - `python3 scripts/bump_version.py <version> --check` (Python, npm, Cargo, Tauri, OpenAPI, VS Code 매니페스트 전체 일치 확인),
     - `node scripts/check_current_release_docs.mjs` (릴리스 문서·체인지로그·증적 일치 확인),
     - `node scripts/check_doc_languages.mjs` (영문/한국어 문서 언어 분리 순도 및 100% 양방향 패리티 확인).
3. **중복 빌드 없는 CD (`publish.yml`):**
   - `release.yml`이 GitHub Release에 정확한 버전 아티팩트와 `SHA256SUMS-<version>.txt`를 업로드한 뒤 `workflow_call`로 `publish.yml`을 자동 호출합니다.
   - `publish.yml`은 업로드된 아티팩트를 내려받아 SHA-256 검증 후 PyPI·npm·VS Code Marketplace·Open VSX에 배포하며, DMG가 이미 존재하면 추가 macOS 러너를 할당하지 않습니다.

전체 릴리스 체크리스트는 [`RELEASE.md`](./RELEASE.md)를 참고하세요.

## `main` 권장 브랜치 보호 규칙

필수 통과 체크(차단):
- `Frontend, extensions & docs`
- `Test (Python 3.11 on ubuntu-latest)`
- `Test (Python 3.12 on ubuntu-latest)`
- `Test (Python 3.14 on ubuntu-latest)`
- `Test (Python 3.11 on macos-latest)`
- `Integration smoke test`
- `Build package`
- `Rust (lint & core crates)`
- `Rust (agent, platform, chat & host)`
- `Rust (Windows workspace tests)`
- `Desktop shell (Tauri check & tests)`
- `Docker image build smoke`
- `Playwright visual smoke`

참고 / 예약 체크:
- `Python dependency audit (pip-audit)`
- `npm dependency audit`
- `Nightly Real-Model RAG Eval`

## 정직한 한계

- 브랜치 보호 규칙은 워크플로우 파일이 아닌 GitHub 저장소 설정에 저장됩니다.
- `APPLE_CERTIFICATE` 시크릿이 없는 호스팅 macOS 러너에서는 ad-hoc 서명 DMG가 생성되며, Developer ID 서명 및 공증은 시크릿 설정 시 자동 활성화됩니다.
- 의존성 보안 감사는 상위 취약점 DB(OSV, npm 레지스트리)에 의존합니다.
