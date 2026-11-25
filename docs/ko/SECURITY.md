[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../../SECURITY.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./SECURITY.md)

# 보안 정책 (Security Policy)

> **상태: 정본(canonical)** — 현재 릴리스와 동기화된 최신 보안 모델입니다.

현재 릴리스: **14.1.0 — Living Synthesis**.

## 지원 버전

공개 Git 트리는 **13.0.0부터 14.1.0까지**의 릴리스 이력을 유지합니다(최신 Rust 호스트 및 도메인 그룹 아키텍처 세대). 보안 지원 역시 동일한 제품 세대를 따르며 **14.x 및 13.x 라인이 보안 수정을 받습니다.**

| 버전 | 지원 여부 |
| --- | --- |
| 14.1.x (최신) | 지원 |
| 14.0.x | 지원 |
| 13.9.x | 지원 |
| 13.8.x | 지원 |
| 13.7.x | 지원 |
| 13.6.x | 지원 |
| 13.5.x | 지원 |
| 13.4.x | 지원 |
| 13.0.x | 지원 |
| 13.0.0 미만 | 미지원 |

## 보안 취약점 제보

보안 이슈는 공개 이슈로 등록하지 말고 아래 주소로 비공개 제보해 주세요.

**rnlgnquvk@gmail.com**

제보 시 포함할 내용:

- 취약점 유형 및 영향 범위
- 재현 절차 또는 개념 증명(PoC)
- 영향을 받는 버전
- 크레딧 표기를 원할 경우 연락처 정보

최초 응답 목표 시간은 48시간입니다.

## 보안 모델

Lattice AI는 로컬 우선(local-first) Digital Brain입니다. 사용자 지식, 대화 컨텍스트, 지식 그래프 데이터, 아카이브를 기본값으로 로컬에 보관하며 모든 외부 통신 경로는 명시적 옵트인을 통해서만 활성화됩니다.

Rust 제품 서버(`lattice-host`)가 **422개 네이티브 오퍼레이션(41개 라우트 패밀리)** 전반의 모든 신뢰 경계를 소유하고, 루프백 전용 Python AI 연산 워커(`latticeai.worker_app`)는 **20개** 허용 목록 연산 라우트(`rust/fixtures/worker_allowlist.json`, Python·Rust 양측 고정; 목록 외 비네이티브 경로는 `404`)로 엄격히 제한됩니다. 보안 책임은 명확한 도메인 주소에 매핑됩니다:

- `rust/lattice-auth/src/` — 버전화 `$scrypt$` 비밀번호 해싱, 상한 메모리 레이트 리미팅, 세션 관리, 구조화된 CSRF Origin 강제(`CsrfReason` + `messages.rs`).
- `rust/lattice-agent/src/kernel/` — 권한 게이트, 모드 불변 서킷 브레이커, fail-closed `NEEDS_REVIEW` 검증, 내구성 워크플로 승인.
- `rust/lattice-agent/src/parse/` 및 `content/sanitize/` — 신뢰할 수 없는 모델 출력 파싱 및 쓰기 전 바이트 정제.
- `rust/lattice-agent/src/tools/safe_io.rs` — OS 수준 TOCTOU 방지 워크스페이스 I/O(Unix `openat` + `O_NOFOLLOW` + `fstat` 루트 고정; Windows 컴포넌트별 핸들 고정 `CreateFileW` + `FILE_FLAG_OPEN_REPARSE_POINT` + `GetFinalPathNameByHandleW`).
- `rust/lattice-core/src/{graph_write/,integrity/,redact.rs}` — 단일 라이터 SQLite 풀(`Store::write_conn`), 워크스페이스 범위 무결성 검사·복구, 컴파일된 `OnceLock` PII/비밀 마스킹.

### 기본 보안 설정

| 설정 | 기본값 | 비고 |
| --- | --- | --- |
| 바인드 주소 | `127.0.0.1` | 명시적 변경 없이는 로컬 전용; 비루프백 바인딩은 `LATTICEAI_REQUIRE_AUTH=true` 없이 기동 거부 |
| 인증 (Auth) | 루프백 로컬 모드 꺼짐 | 공개 또는 비루프백 바인딩 시 강제 활성화 |
| CORS | localhost 전용 | 네트워크 CORS는 명시적 옵트인 필요 |
| 세션 TTL | 24시간 슬라이딩 | 비활성 세션 자동 만료 |
| 비밀번호 해싱 | 버전화 `$scrypt$` | `Result` 반환 `hash_password`(`$scrypt$n=14,r=8,p=1$...`), 레거시 `{salt}:{key}` 검증 호환 유지 |
| 레이트 리미팅 | 상한 인메모리(`10,000` 키) | 만료 우선·최고령 축출, 뮤텍스 포이즈닝 복구, `check_ip`/`enforce` 통합 HTTP `429` + `Retry-After` |
| CSRF 보호 | 엄격 Origin 가드 | 구조화된 `CsrfReason` 열거형(`csrf.rs`) 및 중앙 영문 오류 상수(`messages.rs`: `CSRF_DENIED_DETAIL`, `CSRF_ERROR_CODE`) |
| 데스크톱 Tauri 포트 & CSP | `PortPolicy::PinnedProduction` (`127.0.0.1:4825`) | 프로덕션 빌드는 포트 `4825`를 엄격 고정하며 충돌 시 조용한 폴백 없이 즉시 실패(fail-fast)하여 `'unsafe-inline'` 없는 CSP 및 `capabilities/default.json`과 오리진 불일치 방지 |
| API 키 저장 | 가능 시 OS 키링 | 의도적 평문 비밀 저장 금지 |
| 인스톨러 & `/setup/install` | 확인 토큰 및 허용 목록 게이트 | 마스킹된 명령 계획, 로컬 프로세스 감사, 서버 도출 `brew`/`pip`/`uv` 허용 목록만 실행 |
| 폴더 정리(삭제 파일 정리) | Dry-run | `POST /api/ingestion/folder/prune`은 명시적 `confirm` 시 그래프 서브트리만 제거(디스크 미수정) |
| Brain 저장소 | SQLite WAL | 단일 라이터 `GraphWriter`; PostgreSQL은 `Planned` |
| 채팅 자동 파일 읽기 | 비활성 | 임의 경로 읽기는 명시적 승인 필요 |
| 레거시 전역 그래프 읽기 | 비활성 | 호환성 옵션(`include_legacy_global`) 명시 시에만 허용 |
| 초대(Invitation) 인증 | 서명 및 만료 적용 | 정적 인증 쿠키 및 내장 기본 초대 코드 거부 |

### 인증, 세션 및 CSRF

- 비밀번호는 `lattice-auth::password`의 버전화 `$scrypt$` 해싱을 사용하며 패닉 `.expect()` 없이 `Result<String, PasswordError>`를 반환하고, 레거시 `{salt}:{key}` 해시와의 검증 호환성을 유지합니다.
- 세션 쿠키는 `HttpOnly`와 `SameSite=Lax`이며, 비루프백/공개 배포에서는 `Secure`를 함께 설정합니다.
- 세션 토큰은 로컬에 SHA-256 해시로만 저장되며 삭제·비활성화된 계정은 다음 요청에서 즉시 세션이 무효화됩니다.
- `sessions.json`의 유일한 쓰기 주체는 `lattice-auth`이며, Python 워커는 `mtime_ns`/`size` 변경 시(초당 1회 스로틀링) 재조회하여 부팅 후 로그인도 안전하게 인식합니다.
- POSIX 파일시스템에서 로컬 데이터 디렉터리는 `0700`, 원자적 JSON/세션 파일은 `0600` 권한으로 생성됩니다.
- `rust/lattice-auth/src/csrf.rs`는 구조화된 `CsrfReason` 열거형과 `rust/lattice-auth/src/messages.rs`의 상수(`CSRF_DENIED_DETAIL`, `CSRF_ERROR_CODE`)로 바이트 단위 일관된 HTTP `403` 응답을 반환합니다.
- 초대 인증은 HMAC 서명, 만료 시간 제한, 서버 바인딩을 적용합니다.

### 파일, 워크스페이스 및 아카이브 안전성

- **사용자별 `AGENT_ROOT` 격리**: `IsolatedRootSpec`과 `resolve_isolated_agent_root`가 서버 측에서 `(user_id, tenant_id, workspace_id)` 단위로 작업 디렉터리를 분리하며, 공유 워크스페이스 멤버십이 검증되지 않으면 fail-closed(`workspace_access_denied`)합니다.
- **TOCTOU 방지 워크스페이스 I/O**: `rust/lattice-agent/src/tools/safe_io.rs`가 Unix에서는 `openat` + `O_NOFOLLOW` + `fstat`, Windows에서는 컴포넌트별 핸들 고정 `CreateFileW` + `GetFinalPathNameByHandleW`로 심볼릭 링크 탈출과 경로 경합을 차단합니다.
- **워크스페이스 소유권 강제**: 무결성 복구, 엔티티 병합·롤백, 지식 교정은 모든 대상의 워크스페이스 소유권을 검증해 교차 워크스페이스 요청을 HTTP `403`으로 차단하며, 전역 복구는 명시적 `require_admin` 권한이 필요합니다.
- **`.latticebrain` 아카이브 암호화**: Rust(`lattice-platform::knowledge::portability::{archive, crypto}`)에서 RFC 7914 `scrypt` 암호문구 또는 X25519 + HKDF-SHA256 수신자 봉인, 상수 시간 MAC 검증(`ct_eq`)이 포함된 Encrypt-then-MAC(`SHA256-CTR` + `HMAC-SHA256`), ZIP 경로 탐색 차단(`validate_zip_entries`)을 수행합니다. 복원 시 세대 에폭을 올려 재시작 없이 즉시 반영됩니다.
- **데스크톱 및 확장 보안**: Tauri 프로덕션 빌드는 `PortPolicy::PinnedProduction`으로 `127.0.0.1:4825`를 고정해 포트 충돌 시 조용한 `4826+` 스캔 대신 실행 가능한 `gateway_error` 안내로 즉시 실패하며, 게이트웨이 생명주기를 직렬화하고 `'unsafe-inline'` 없는 CSP를 강제합니다. `vscode-extension`은 선택적 Bearer 인증과 TTL 단일 사용 승인 토큰 캐시를 적용하고, `browser-extension`은 포트 `4825` 한정 권한과 승인 결정 비노출 원칙을 유지합니다.
- **정적 보안 분석 및 공급망 게이트**: Python CI는 Ruff `"BLE"`(포괄적 `except Exception` 금지), McCabe `max-complexity <= 20`, 프로덕션 `S608` SQL 규칙, 최소 범위 Bandit 파일별 예외(`S603`, `S105`, `S108`)를 강제합니다. 모든 GitHub Actions `uses:`는 불변 커밋 SHA로 고정되고, 멀티스테이지 `Dockerfile`은 모든 베이스 이미지를 SHA256 다이제스트로 고정합니다.

### 에이전트 및 도구 안전성

- `AgentRuntime` 미리보기/준비 점검은 도구를 실행하지 않으며, 자동 승인 대상이 아닌 계획은 명시적 사람 승인이 필요합니다.
- 모든 상태 변경 도구는 `MUTATING_TOOL_INVENTORY`로 추적됩니다. 기존 파일 수정·삭제는 원본 콘텐츠 SHA를 기록하는 `change_proposal`로 스테이징되며 동시 수정 충돌 시 `409`로 fail-closed 합니다.
- 검증기는 `standard`/`compact`/`guided` 모든 프로필에서 검증 불가·실패 결과를 `NEEDS_REVIEW`로 fail-closed 처리합니다.
- `guided` 프로필은 JSON 포맷 요구만 제거할 뿐 권한 게이트·루프 가드·쓰기 전 스냅샷·`sanitize_write_content`를 동일하게 유지하며 예시 프롬프트 복사(`COPIED_EXAMPLE`)를 거부합니다.
- 명령 실행 도구는 고정된 실행 파일 허용 목록과 정제된 환경을 사용하며 인터프리터·경로 탐색·심볼릭 링크 탈출·위험 `find`/ripgrep 옵션을 거부합니다.
- `POST /mcp`는 OpenAPI 계약 내부에 마운트되어 에이전트 카탈로그와 동일한 `check_governance`를 공유하며, MCP·플러그인은 전용 승인 토큰 없이 로컬 파일/문서 도구를 호출할 수 없습니다.
- 비밀 값과 PII는 로그, 감사 페이로드, `LoopTrace`, 클라우드 이그레스, 포스처 게이트된 `GET /host/diagnostics` 출력 전에 `lattice-core::redact`에서 중앙 마스킹됩니다.

### 외부 통신

기본 텔레메트리는 없습니다. 클라우드 모델 호출, 모델 다운로드, Brain Network 피어 교환, 업데이트 확인, 마켓플레이스 새로고침, 동의된 `/setup/install` 패키지 설치는 모두 명시적 설정과 사용자·관리자 실행이 필요합니다.

- **감사 및 제안 기반 클라우드 턴**: 모든 클라우드 턴은 양 모드 모두에서 `sensitive`/`private`/`do_not_share` 노드와 비밀 파일을 차단하고 PII/비밀을 마스킹하며, 상한 토큰 예산과 형태 전용 `cloud_egress` 감사 기록을 남긴 뒤 추출 지식을 `kg_cloud_expansion` 검토함 제안으로 스테이징합니다(`auto_commit` 기본값 `false`). 요청 단위 `network_mode: "local_only"`가 항상 우선합니다.
- **이중 클라우드 자격 증명**: `api_key`(OpenAI 호환, 무과금 `GET /models` 라이브 프로브)와 `cli_oauth`(중립 임시 디렉터리·120초 타임아웃의 로컬 OAuth `agy`/`grok`)를 지원합니다.
- **웹 페이지 캡처**: 공인 IP를 해석·고정하고 사설/예약 대역을 거부하며 모든 리다이렉트를 재검사하고 프록시 환경 변수를 무시하며 텍스트 스트림을 4 MiB로 제한합니다.

### 보안 민감 환경 변수

| 변수 | 필수 여부 | 용도 |
| --- | --- | --- |
| `LATTICEAI_PERMISSION_UI_URL` | 선택 | 권한 검토 UI 기본 URL(승인 토큰은 절대 첨부하지 않음) |
| `LATTICEAI_REQUIRE_AUTH` | 선택, 루프백 기본 꺼짐 | 로컬 모드에서도 인증 강제(공개 또는 비루프백 바인딩 시 필수) |
| `LATTICEAI_INVITE_GATE_ENABLED` | 선택, 기본 꺼짐 | 무제한 회원가입을 열지 않고 서명 초대 온보딩 활성화 |
| `LATTICEAI_INVITE_CODE` | 초대 게이트 활성화 시 권장 | 운영자가 생성한 무작위 초대 코드(미지정 시 설치별 비공개 값 자동 생성, 공용 기본값 없음) |

## 공개 배포 가이드

Lattice AI를 `localhost` 외부에 노출할 경우:

1. `LATTICEAI_MODE=public` 및 `LATTICEAI_REQUIRE_AUTH=true`를 설정하세요.
2. 일반 회원가입을 닫아 두고 추가 사용자 온보딩이 필요할 때만 서명 초대 게이트를 사용하세요.
3. 리버스 프록시(nginx / Caddy) 앞단에서 HTTPS를 종단하고 `CSRF_TRUSTED_ORIGINS`를 구성하세요.
4. 영구 스토리지를 마운트하고 방화벽에서 포트 `4825` 접근을 프록시로 제한하세요.
5. 배포 전 `ALLOWED_COMMANDS`, CORS, 레이트 리미트, 그래프 노출 설정을 점검하세요.

## 알려진 제한 사항

- 로컬 파일 보안은 사용자의 OS 계정, 디스크 암호화, 백업 정책에 의존합니다.
- 클라우드 모델 프롬프트는 사용자가 명시적으로 활성화한 뒤 해당 공급자의 정책을 따릅니다.
- 신뢰된 로컬 관리자는 Lattice AI 외부에서 로컬 파일과 프로세스 메모리를 검사할 수 있습니다.
- Hugging Face 모델 다운로드: `scripts/verify_hf_model_registry.py`는 가중치 다운로드 없이 Hub 메타데이터를 정적 검증하고 `EmbeddingIndexContract`는 `model_revision`을 기록하지만, `latticeai/services/model_runtime/download.py`와 `latticeai/core/embedding_providers/text.py`에는 아직 모든 다운로드 경로에 불변 기본 커밋 `revision=` 고정이 적용되지 않았습니다([`docs/SECURITY_AUDIT.md`](../SECURITY_AUDIT.md)에 Bandit `B615`로 추적 중).
