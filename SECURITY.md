# Security Policy

## 지원 버전

현재 활발히 유지되는 버전:

| 버전 | 지원 여부 |
|------|-----------|
| 7.5.x (latest) | ✅ 지원 |
| 7.4.x | ✅ 지원 |
| 7.3.x | ✅ 보안 패치 범위 내 지원 |
| 7.2.x | ✅ 보안 패치 범위 내 지원 |
| 7.1.x | ✅ 보안 패치 범위 내 지원 |
| 7.0.x | ✅ 보안 패치 범위 내 지원 |
| 6.7.x | ✅ 보안 패치 범위 내 지원 |
| 6.6.x | ✅ 보안 패치 범위 내 지원 |
| 6.5.x | ✅ 보안 패치 범위 내 지원 |
| 6.4.x | ✅ 보안 패치 범위 내 지원 |
| 6.3.x | ✅ 보안 패치 범위 내 지원 |
| 6.2.x | ✅ 보안 패치 범위 내 지원 |
| 6.1.x | ✅ 보안 패치 범위 내 지원 |
| 6.0.x | ✅ 보안 패치 범위 내 지원 |
| 5.6.x | ✅ 보안 패치 범위 내 지원 |
| 5.5.x | ✅ 보안 패치 범위 내 지원 |
| 5.4.x | ✅ 보안 패치 범위 내 지원 |
| 5.2.x | ✅ 보안 패치 범위 내 지원 |
| 5.1.x | ✅ 보안 패치 범위 내 지원 |
| 5.0.x | ✅ 보안 패치 범위 내 지원 |
| 4.7.x | ✅ 보안 패치 범위 내 지원 |
| 4.6.x | ✅ 보안 패치 범위 내 지원 |
| 4.5.x | ✅ 보안 패치 범위 내 지원 |
| 4.4.x | ✅ 보안 패치 범위 내 지원 |
| 4.3.x | ✅ 보안 패치 범위 내 지원 |
| 4.2.x | ✅ 보안 패치 범위 내 지원 |
| 4.1.x | ✅ 보안 패치 범위 내 지원 |
| 4.0.x | ✅ 보안 패치 범위 내 지원 |
| 3.6.x | ✅ 보안 패치 범위 내 지원 |
| 3.5.x | ✅ 보안 패치 범위 내 지원 |
| 3.4.x | ✅ 보안 패치 범위 내 지원 |
| 3.3.x | ✅ 보안 패치 범위 내 지원 |
| 3.2.x | ✅ 보안 패치 범위 내 지원 |
| 3.1.x | ✅ 보안 패치 범위 내 지원 |
| 3.0.x | ✅ 보안 패치 범위 내 지원 |
| 2.2.x | ✅ 보안 패치 범위 내 지원 |
| 2.0.x | ✅ 보안 패치 범위 내 지원 |
| 1.7.x | ✅ 보안 패치 범위 내 지원 |
| 1.6.x 이하 | ❌ 미지원 |

## 취약점 제보

보안 취약점을 발견하셨다면 **공개 이슈 대신** 아래 이메일로 비공개 제보해 주세요:

**rnlgnquvk@gmail.com**

제보 시 다음 정보를 포함해 주세요:

- 취약점 유형 및 영향 범위
- 재현 단계 (PoC 코드 포함 시 빠른 처리 가능)
- 영향을 받는 버전
- 발견하신 분의 연락처 (크레딧 포함 원하시는 경우)

일반적으로 **48시간 이내** 최초 응답을 드립니다. 패치 후 CVE 등록 및 크레딧 표기를 지원합니다.

## 보안 모델

Lattice AI v7.5.0는 모델이 바뀌어도 사용자의 지식과 맥락을 보존하는 local-first Digital Brain입니다. Personal /
Organization Workspace, Brain-first Conversation, Knowledge Graph, Vector
Index, Hybrid Search, Basic / Advanced / Admin mode, durable workspace
governance, independent Brain Core package boundary, pluggable storage,
encrypted `.latticebrain` archives, confirmed restore/import, local-only
startup hardening, desktop sidecar status, strict packaged-app CSP, first-run
setup, saved-profile email/password mismatch guards, explicit model
recommendation/install/validation flow, and default-off model downloads/runtime
installs를 포함합니다. v7.5.0는 v6.4.0의 workspace-scoped graph/search/memory
retrieval and mutation boundary를 유지하고, fallback embedding label,
drift/re-index signal, structured context attribution/guardrail을 통해
unsupported/stale Brain facts를 확정 사실처럼 제시하지 않도록 합니다. 일반 사용자 Brain 화면과
관리자 로그/보안/운영 화면을 분리하고, Brain proof API가 active model id와
recall sample만 노출하도록 scope/read gates를 통과하게 하며, 로컬 파일 자동 읽기 우회를 차단하고,
secret redaction을 로그/감사/보안 export/hook packet에 중앙 적용합니다. v7.5.0은
visible product IA와 legacy compatibility routes를 분리하고 rich pages를 lazy-load해,
보안/설정/모델/그래프 화면이 Brain Home에서 명확히 도달되면서도 초기 surface에
불필요하게 모두 적재되지 않게 합니다. 또한 VS Code extension heartbeat/status
endpoint와 shell sync indicator는 연결/인덱싱/동기화 상태만 노출하고, 파일 내용은
사용자 명시 action(`Send To Lattice`, `Ask Current File`)에서만 workspace bridge로
전송합니다. AgentRuntime preview는 run row 생성이나 tool execution 없이 실행 준비 상태와
blocking reason만 반환하고, ToolRegistry diagnostics는 handler/governance/catalog
정렬 상태와 permission projection만 노출합니다. 공통 agent-run contract family는 agent run,
workflow run, audit event, realtime event의 runtime 종류, mode, status, role/timeline,
terminal 여부를 명시하고 audit event는 redaction 이후 contract를 생성해 simulated output이나
민감 payload가 real execution evidence처럼 보이지 않도록 한다. AgentRuntime과 realtime feed는
compact contract view를 노출해 UI/API consumers가 surface별 payload를 재해석하지 않아도 같은
보안 계약을 읽을 수 있게 한다. 아래
보안 모델을 따릅니다:

### 기본 안전 설정 (Default Secure)

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 바인딩 주소 | `127.0.0.1` | 로컬 전용. 네트워크 노출 시 명시적으로 `LATTICEAI_HOST=0.0.0.0` |
| 인증 | `REQUIRE_AUTH=true` | 모든 민감 엔드포인트 로그인 세션 필요 |
| CORS | localhost만 허용 | 외부 도메인 허용 시 `LATTICEAI_CORS_ALLOW_NETWORK=true` |
| 세션 TTL | 24시간 (sliding) | 비활동 시 자동 만료 |
| API 키 저장 | OS keyring | 평문 디스크 저장 없음 |
| 기본 Brain storage | SQLite | 로컬 파일. Postgres는 명시적 opt-in |
| Docker Postgres setup | 비활성 | API/UI에서 명시적 consent 없이는 시작하지 않음 |
| Tauri production CSP | strict local-only policy | `csp:null` 금지, 외부 script/frame/object 기본 차단 |
| Chat auto file read | 비활성 | `LATTICEAI_AUTO_READ_CHAT_PATHS` 기본 false, true여도 승인 없는 임의 경로 읽기 차단 |

### 인증 및 세션

- 비밀번호: scrypt 해싱
- 세션 쿠키: `HttpOnly + SameSite=Lax` (CSRF 방어)
- 세션 파일: `~/.ltcai/sessions.json` (서버 로컬 저장)
- SSO: Entra ID / Okta OIDC 지원

### 파일 업로드 보안

- magic-number 시그니처 검증 (PDF/DOCX/XLSX/PPTX/PNG/JPEG)
- 확장자 위조 차단
- 업로드 파일은 `~/.ltcai/knowledge_graph_blobs/`에 격리 저장

### Rate Limiting

- `/chat`: 30 req/분 (per user)
- `/agent`: 6 req/분 (per user)
- `/upload`: 12 req/분 (per user)
- `LATTICEAI_RATE_LIMIT=0`으로 비활성화 가능

### 에이전트 도구 샌드박스

- `run_command()`: 위험 플래그(`--rm -rf`, `sudo` 등) 차단
- `edit_file()`: `old_string` 유일성 검증, 샌드박스 이탈 차단
- `grep()`, `read_file()`: `node_modules`, `.git`, `venv`, `dist` 자동 제외
- Agent context packets redact obvious secret fields (`token`, `password`,
  `api_key`, `credential`) before persistence/replay.
- Secret-like values are centrally redacted before logs, audit payloads,
  security exports, frontend previews, and hook packets.
- Telegram bot tokens, provider tokens, webhook URLs, client secrets, and
  Postgres DSNs are masked before exception or response text is written to logs.
- Agent handoff, review/retry, workflow, plugin, and execution-failure events
  are workspace-scoped in the existing SSE activity feed.

### 텔레메트리 및 외부 통신

기본 telemetry는 없습니다. 모든 데이터(채팅, 모델, 설정, 지식 그래프)는
로컬(`~/.ltcai/`, `~/.ltcai-brain/`)에 저장됩니다. 기본 시작 시 외부 서버로
데이터를 전송하지 않습니다. 사용자가 직접 클라우드 API 키를 설정하고 클라우드
모델 실행을 선택한 경우 해당 제공업체에 프롬프트가 전송될 수 있습니다.
Telegram, Brain Network, Docker setup, model downloads, cloud model calls,
remote marketplace refreshes, and update checks는 명시적 opt-in 경로를
요구합니다. Token/API key 존재만으로 외부 통신을 시작하지 않습니다.

## 퍼블릭 배포 시 권고사항

Render, Fly.io 등 퍼블릭 환경에 배포할 경우:

1. `LATTICEAI_MODE=public` 설정
2. `LATTICEAI_INVITE_CODE` 비공개 값으로 설정
3. HTTPS 리버스 프록시(nginx, Caddy) 앞에 두기
4. `LATTICEAI_ENABLE_GRAPH=false` (Data Graph 비공개 시)
5. 영구 볼륨 마운트 (`/data`)

자세한 내용: [docs/public-deploy.md](docs/public-deploy.md)

## 알려진 제한사항 (Out of Scope)

- 에이전트가 `run_command()`를 통해 임의 셸 명령을 실행할 수 있음 (설계상 의도). 신뢰하지 않는 사용자에게 에이전트 접근 권한 부여 시 주의.
- MLX 모델 파일 자체의 무결성 검증은 provider metadata에 의존합니다.
- Cloud model prompts follow the selected provider's privacy and retention
  policy once the user explicitly chooses that provider.
- 로컬 파일 보안은 사용자 OS 계정, 디스크 암호화, 백업 정책에도 의존합니다.
