# Security Policy

## 지원 버전

현재 활발히 유지되는 버전:

| 버전 | 지원 여부 |
|------|-----------|
| 2.2.x (latest) | ✅ 지원 |
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

Lattice AI v2.2.1은 local-first AI 워크스페이스 / 지식 그래프 플랫폼으로, 아래 보안 모델을 따릅니다:

### 기본 안전 설정 (Default Secure)

| 설정 | 기본값 | 설명 |
|------|--------|------|
| 바인딩 주소 | `127.0.0.1` | 로컬 전용. 네트워크 노출 시 명시적으로 `LATTICEAI_HOST=0.0.0.0` |
| 인증 | `REQUIRE_AUTH=true` | 모든 민감 엔드포인트 로그인 세션 필요 |
| CORS | localhost만 허용 | 외부 도메인 허용 시 `LATTICEAI_CORS_ALLOW_NETWORK=true` |
| 세션 TTL | 24시간 (sliding) | 비활동 시 자동 만료 |
| API 키 저장 | OS keyring | 평문 디스크 저장 없음 |

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
- Agent handoff, review/retry, workflow, plugin, and execution-failure events
  are workspace-scoped in the existing SSE activity feed.

### 텔레메트리

없음. 모든 데이터(채팅, 모델, 설정, 지식 그래프)는 로컬(`~/.ltcai/`, `~/.ltcai-brain/`)에만 저장됩니다. 외부 서버로 어떠한 데이터도 전송되지 않습니다. (단, 사용자가 직접 클라우드 API 키를 설정한 경우, 해당 클라우드 제공업체에 프롬프트가 전송됩니다.)

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
- MLX 모델 파일 자체의 무결성 검증 미지원 (Hugging Face 등 신뢰할 수 있는 출처에서 다운로드 권장)
