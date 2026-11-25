[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v14.1.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v14.1.0.md)

# Lattice AI v14.1.0 — Living Synthesis (2026-11-25)

## 주요 변경 사항

### 1. v14.0.0 치명적 회귀 2건 수정
- **Rust ↔ Python UTC 타임스탬프 통합 (`rust/lattice-core/src/graph_write/clock.rs`, `lattice_brain/utils.py`, `lattice_brain/graph/_kg_fsutil.py`):**
  - Python의 `local_now()`, `now_iso()`, `_recency_score()`, `_safe_iso_from_stat_mtime()`을 Rust `SystemClock::now_iso()`(`gmtoff: 0`)와 동일한 오프셋 없는 UTC(`YYYY-MM-DDTHH:MM:SS`)로 통일하여 UTC+9 등 비-UTC 환경에서 발생하던 9시간 타임존 편차를 제거했습니다.
  - `clock.rs`, `workspace/pyutil.rs`, `retrieval/routes/mod.rs`, `chat/turn.rs`, `command_center_api/store.rs`의 모순된 v14.0.0 이전 주석을 정리하고, `Asia/Seoul`(UTC+9)·`America/New_York`(UTC-5)·`UTC` 환경에서 편차가 없음을 검증하는 Rust/Python 단위 테스트(`tests/unit/test_timestamp_utc_consistency.py`)를 추가했습니다.
- **Tauri 프로덕션 포트 고정 정책 정렬 (`src-tauri/src/topology.rs`, `src-tauri/src/backend.rs`):**
  - `PortPolicy`(`PinnedProduction` / `AllowDynamicFallback`)를 도입해 프로덕션 빌드는 `tauri.conf.json` CSP 및 `capabilities/default.json` 원격 IPC 정책과 일치하는 `PINNED_PRODUCTION_PORT`(`4825`)로 엄격히 고정했습니다.
  - 포트 `4825` 점유 시 프로덕션에서 도달 불가능한 `4826+` 포트로 조용히 폴백하던 동작을 제거하고, 명확한 사용자 안내 메시지와 함께 즉시 실패(fail-fast)하도록 수정했으며 충돌/폴백/설정 일치 단위 테스트를 추가했습니다.

### 2. 풀스택 기술 부채 해소 및 정적 분석 강화
- **Python 예외 처리 세분화 및 Ruff 규칙 강화 (`pyproject.toml`, `lattice_brain/`, `latticeai/`, `scripts/`, `tests/`):**
  - `lattice_brain/multimodal/images.py`(6곳), `latticeai/models/router/loading.py`(6곳) 및 저장소 전반의 포괄적 `except Exception` / `# noqa: BLE001` 블록을 구체적 예외 타입으로 모두 좁혔습니다.
  - `[tool.ruff.lint] select`에 `"BLE"`를 전역 추가하고, `[tool.ruff.lint.mccabe] max-complexity`를 `30`에서 `20`으로 강화(초과 함수 12개를 응집도 높은 헬퍼로 리팩터링)했으며, 프로덕션 `S608`을 유지한 채 `[tool.ruff.lint.per-file-ignores]`의 불필요한 `S603`, `S105`, `S108` 예외를 최소 범위로 축소했습니다.
- **Rust 인증, Tauri 커맨드 시그니처 및 문서 주석 정리:**
  - `rust/lattice-auth/src/csrf.rs`의 `DENIED_DETAIL`과 오류 코드를 `messages.rs`(`CSRF_DENIED_DETAIL`, `CSRF_ERROR_CODE`)로 이동하고, 사유 문자열을 구조화된 `CsrfReason` 열거형으로 교체했습니다(HTTP 403 JSON 계약 바이트 단위 유지).
  - `src-tauri/src/main.rs`의 비실패 커맨드 `backend_status`, `shutdown_backend`가 불필요한 `Result<BackendStatus, String>` 대신 `tauri::AppHandle`을 통해 `BackendStatus`를 직접 반환하도록 정렬했습니다.
  - `lattice_brain/quiet.py` 최상단 독스트링을 `lattice_brain.quiet`이 단일 원천(Single Source of Truth)임을 반영하도록 갱신했습니다.
- **TypeScript 7 `noUncheckedIndexedAccess` 활성화 (`tsconfig.json`, `frontend/src/`):**
  - `"noUncheckedIndexedAccess": true`를 활성화하고 프론트엔드 소스·테스트 68개 파일의 TypeScript 오류 457건을 모두 해결하면서도 Vitest **100% 커버리지**(구문·분기·함수·라인, 113개 테스트 파일 / 1,852개 테스트)를 유지했습니다.
- **PR 시각 회귀 게이트 활성화 (`.github/workflows/ci.yml`):**
  - Playwright `visual` 스모크 잡이 `main` 푸시뿐 아니라 PR에서도 실행되도록 변경해 머지 전 시각 회귀를 차단합니다.

### 3. 문서 최신화 (`v14.1.0`)
- 저장소 루트, `docs/`, 한국어 미러(`README.ko.md`, `docs/ko/`) 전반의 마크다운 문서를 `v14.1.0` 기준으로 갱신하고, v13 이전 구형 서술과 이미 완료된 `v12.x` 로드맵 항목을 정리하며 100% 한↔영 양방향 패리티를 유지했습니다.
