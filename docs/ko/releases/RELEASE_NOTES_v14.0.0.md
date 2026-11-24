[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../releases/RELEASE_NOTES_v14.0.0.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./RELEASE_NOTES_v14.0.0.md)

# Lattice AI v14.0.0 — Living Synthesis (2026-11-25)

## 주요 변경 사항

### 1. Rust 호스트, 인증 및 서브 크레이트 고도화
- **버전화 `$scrypt$` 비밀번호 해싱 (`rust/lattice-auth/src/password.rs`):** 패닉을 유발하던 `.expect()`를 `Result<String, PasswordError>`로 전환하고, 신규 해시를 `$scrypt$n=14,r=8,p=1${salt}:{key}` 형식으로 발급하면서 레거시 `{salt}:{key}` 검증 호환성을 유지하며 `stored_is_hashed` 검증을 강화했습니다.
- **상한 레이트 리미터 및 통합 HTTP `429` 계약 (`rust/lattice-auth/src/{ratelimit.rs,csrf.rs}`):** IP·버킷 맵을 `10,000`개로 제한하고 만료 우선·최고령 항목 축출과 뮤텍스 포이즈닝 복구를 적용했으며, `check_ip`와 `enforce`의 `429` JSON + `Retry-After` 응답을 통일하고 CSRF 거부 메시지를 영문 표준으로 정리했습니다.
- **`libc` `unsafe` 없는 순수 Rust UTC 시계 (`rust/lattice-core/src/graph_write/clock.rs`):** `libc::localtime_r`/`gmtime_r`/`mktime` FFI 호출과 `lattice-core`의 `libc` 의존성을 완전히 제거하고 순수 Rust 달력 연산으로 교체해 macOS·Linux·Windows에서 동일한 동작을 보장합니다.
- **논블로킹 비동기 I/O, 상한 캐시 및 Fail-Closed 게이트웨이:**
  - `lattice-ingest/src/local_files_api/local.rs`의 블로킹 `std::fs` 호출과 `lattice-chat/src/documents.rs`의 세션 영속화를 `tokio::task::spawn_blocking`으로 오프로딩하고 디스크 쓰기 전에 세션 뮤텍스를 해제합니다.
  - `lattice-chat/src/cloud/budget.rs`(`4,096` 스코프)와 `lattice-retrieval/src/native_hnsw.rs`(`32` 그래프)에 LRU 상한 축출과 포이즈닝 복구를 적용했습니다.
  - `lattice-jobs`에서 드레인 중단 시 부분 진행 통계를 보존하고 클레임 해제 오류를 로깅하며 `/host/jobs` 라우트에 루프백/Bearer 심층 방어 가드를 추가했습니다.
  - 무리다이렉트 프록시 클라이언트 생성 실패 시 `GatewayState::with_client`가 fail-closed 하도록 수정하고 `lattice-host`의 `std::env::set_var`를 `EnvOverlayProbe`로 대체했습니다.

### 2. Python AI 워커, Brain 코어 및 패키징
- **플랫폼 안전 패키징 (`pyproject.toml`):** `local`/`all` extra의 `mlx-vlm`/`mlx-lm`에 `; sys_platform == 'darwin' and platform_machine == 'arm64'` 마커를 적용하고 `hnswlib`/`pyautogui`를 `all`에 추가했으며, 빈 extra를 제거하고 Changelog URL을 루트 `CHANGELOG.md`로 교정하고 Ruff 보안 규칙(`S608`, `S105`, `S108`, `S603`, `S604`, `S607`)을 재활성화했습니다.
- **워커·브레인 안정성:** `lattice_brain/graph/runtime.py`에 `ContextVar` 기반 `llm_router_scope` DI를 추가하고, `lattice_brain/embeddings.py`에 안전한 `LATTICEAI_VECTOR_DIM` 파싱·사전 컴파일 정규식·`embed_async`를 도입했으며, `lattice_brain/quiet.py`의 `record_suppressed`로 중복을 제거하고 `latticeai/core/security.py`에 버킷 상한 및 잘못된 CIDR 경고를 추가했습니다.

### 3. 엄격 TypeScript 7 프론트엔드, Tauri 데스크톱 및 확장
- **프론트엔드 (`frontend/src/`, `tsconfig.json`, `package.json`):** `noImplicitReturns`, `noFallthroughCasesInSwitch`, `noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`를 활성화하고 `AppRouteView.tsx`로 라우팅을 모듈화했으며, 런타임 UI 패키지를 `dependencies`로 이동하고 데스크톱 오리진 캐시 실패 시 재시도를 복구했으며 SSR/비-DOM 안전 가드와 113개 테스트 파일 **Vitest 100% 커버리지**를 유지했습니다.
- **Tauri 2.11 데스크톱 (`src-tauri/`):** `std::env::set_var`와 TOCTOU 경쟁을 제거한 직렬화 비동기 게이트웨이 `start`/`stop`/`restart` 생명주기, 루프백 전용 외부 오리진 검증 및 안전 포트 증가, `spawn_blocking` 기반 `/usr/bin/osascript` 폴더 선택기, `'unsafe-inline'`을 제거한 `127.0.0.1:4825` 고정 권한 및 CSP를 적용했습니다.
- **VS Code 및 브라우저 확장:** `vscode-extension`에 선택적 Bearer 인증, TTL 단일 사용 승인 토큰 캐시, 스트림 타임아웃, 실시간 설정 리로드, 크로스플랫폼 VSIX 패키징(`scripts/vsix-cli.mjs`)을 추가했고, `browser-extension`의 호스트 권한을 포트 `4825`로 축소하고 라이브 DOM 가시성 추출·UTF-8 바이트 예산 절단·종단간 fetch 타임아웃을 적용했습니다.

### 4. 크로스플랫폼 CI/CD, 컨테이너화 및 아키텍처 문서 최신화
- **실제 Windows Rust CI, Tauri CI 및 Docker 스모크 (`.github/workflows/ci.yml`, `Dockerfile`, `.dockerignore`):** `rust-windows`(`windows-latest`), `tauri-check`(`macos-latest`), `docker-smoke` 잡을 추가하고, `taiki-e/install-action` 및 멀티스테이지 `Dockerfile` 베이스 이미지(`node:22-slim`, `rust:1.100-slim-bookworm`, `python:3.11-slim-bookworm`)를 SHA256 다이제스트로 고정했으며 최상위 `permissions: contents: read`를 선언하고 Docker 빌드 안에 프론트엔드 빌드를 통합했습니다.
- **14.0.0 아키텍처 문서 최신화:** `ARCHITECTURE.md`, `docs/ko/ARCHITECTURE.md`, `docs/architecture.md`, `docs/ko/architecture-overview.md` 및 크레이트별 `ARCHITECTURE.md`를 `14.0.0` 기준으로 최신화하고 현재 구조와 맞지 않는 구버전 이력과 삭제된 모듈·별칭 안내를 정리했습니다.
