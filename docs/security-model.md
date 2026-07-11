# Lattice AI — 보안 모델

## 설계 원칙

Lattice AI는 **개인 AI 워크스페이스**로 설계되었습니다. 기본값은 최대한 안전하게, 네트워크 노출은 명시적 opt-in으로만 허용합니다.

## 네트워크 바인딩

| 설정 | 바인딩 | 용도 |
|------|--------|------|
| 기본 | `127.0.0.1:4825` | 로컬 전용, 외부 접근 불가 |
| `LATTICEAI_HOST=0.0.0.0` | `0.0.0.0:4825` | 같은 Wi-Fi 기기 접근 허용 |
| 퍼블릭 배포 | nginx/Caddy 뒤에 두기 | HTTPS 종단 + 리버스 프록시 |

## 인증

### 비밀번호

- scrypt 해싱 (`hashlib.scrypt`, N=2^14, r=8, p=1)
- `users.json`에 `{"hash": "<scrypt hex>"}` 형식 저장
- 평문 비밀번호는 메모리에도 저장되지 않음

### 세션

- `secrets.token_urlsafe`로 생성한 토큰은 SHA-256 해시만
  `~/.ltcai/sessions.json`에 저장(레거시 평문 토큰은 로드 시 자동 마이그레이션)
- TTL: 24시간 + sliding refresh (활동 시 자동 연장, 15분 단위 디스크 쓰기)
- 쿠키: `HttpOnly; SameSite=Lax; Path=/`
- public/non-loopback에서는 `Secure`도 강제(HTTPS 필요)
- 서버 재시작 후에도 유지 (파일 기반)
- 삭제되거나 비활성화된 계정의 기존 세션은 다음 요청에서 즉시 거부
- POSIX에서 데이터 디렉터리 `0700`, atomic JSON/세션 파일 `0600`
- 초대 게이트 쿠키는 설치별 secret으로 HMAC 서명되고 만료 시각을 포함함.
  `authorized=true` 리터럴은 신뢰하지 않으며, 공용 기본 invite code도 없음

### SSO (선택적)

- Entra ID / Okta OIDC (`OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`)
- 콜백 후 내부 세션 토큰으로 변환
- 초대 게이트 활성화 시 신규 SSO JIT 계정은 서명 초대 권한이 필요하며,
  해당 권한은 서버 측 일회용 state/nonce/PKCE 트랜잭션에 바인딩됨
- 어드민 핸드오프: `sessionStorage` 1회 읽기 (URL 파라미터 노출 방지)

## API 키 보안

- OS keyring (macOS Keychain, Windows Credential Manager, Linux Secret Service) 저장
- 평문 디스크 저장은 `LATTICEAI_ALLOW_PLAINTEXT_API_KEYS=true` 명시 시에만
- 채팅 히스토리 저장 전 API key/token/password 패턴 자동 마스킹

## CORS

```python
CORS_ALLOWED_ORIGINS = ["http://localhost:4825", "http://127.0.0.1:4825"]
```

- 기본: localhost만 허용
- `LATTICEAI_CORS_ALLOW_NETWORK=true`: 같은 Wi-Fi 기기 허용
- 퍼블릭 배포: 리버스 프록시 도메인만 허용 권장

## Rate Limiting

토큰 버킷 알고리즘, per-user:

| 엔드포인트 | burst | 지속 |
|-----------|-------|------|
| `/chat` | 30 | 30/분 |
| `/agent` | 10 | 6/분 |
| `/upload` | 20 | 12/분 |

`LATTICEAI_RATE_LIMIT=0`으로 비활성화 (개발 환경용).

## 파일 업로드

```python
MAGIC_NUMBERS = {
    ".pdf":  b"%PDF",
    ".docx": b"PK\x03\x04",
    ".xlsx": b"PK\x03\x04",
    ".pptx": b"PK\x03\x04",
    ".png":  b"\x89PNG",
    ".jpg":  b"\xff\xd8\xff",
    ".zip":  b"PK\x03\x04",
}
```

- 업로드 시 파일 첫 바이트와 확장자 매핑 검증
- 불일치 시 400 에러

## 수집 & 그래프 포터빌리티 보안 (v3.6.0)

- **수집 라이프사이클**: 모든 수집은 `IngestionPipeline.ingest` → `dispatch_tool`
  를 거쳐 `pre_tool`/`post_tool` 훅이 발화됩니다. `pre_tool`이 차단하면 수집은
  정직하게 `status="blocked"`로 거부됩니다(권한 게이트·민감정보 가드 적용).
- **웹 URL 읽기**(`/api/browser/read-url`): `http(s)`만 허용하고 DNS의 모든 결과에서
  loopback/private/link-local/multicast/reserved 주소를 거부합니다. 검증한 IP에 연결을
  고정해 DNS rebinding을 막고, redirect마다 재검증하며 환경 proxy를 사용하지
  않습니다. 12초 타임아웃, 스트리밍된 4MB 응답 상한, textual content type만
  처리합니다. 차단/로그인 필요 페이지는 5xx가 아닌 **422로 실패**합니다.
- **브라우저 탭 수집**(`/api/browser/ingest-current-tab`): payload 정화(스크립트/
  스타일 제거) + 페이로드 크기 상한(413). Manifest V3 확장은 **`127.0.0.1`로만**
  전송하며 클라우드 엔드포인트가 없습니다.
- **포터빌리티 권한**: 상태 읽기는 로그인 사용자, 전체 그래프 export/provenance와
  **import / backup / restore는 admin 전용**(`require_admin`)입니다. 그래프는
  머신-전역 자원입니다.
- **워크스페이스 컨텍스트**: MCP 그래프 호출, 메모리 recall, hybrid search,
  garden-note 컨텍스트, realtime presence는 인증된 사용자와 활성/허용 workspace에
  바인딩됩니다. 에이전트·플러그인 registry 및 graph curation 변경은 admin
  전용이며, MCP 환경 변수 값은 API 응답에 포함되지 않습니다. MCP/플러그인
  실행은 local file/document 도구를 호출할 수 없고 전용 승인 토큰 경로를
  사용해야 합니다. Document RAG와 answer trace도 동일한 workspace 범위로
  검색·저장됩니다.
- **fail-closed KG 범위**: workspace projection 조회 실패 또는 scope를 알 수 없는
  v2 node는 반환하지 않습니다. legacy-global 데이터는
  `include_legacy_global=True`를 명시한 호환 경로에서만 읽습니다.
- **복원 무결성**: 백업 아카이브는 `manifest.json`의 sha256과 대조 검증 후에만
  복원되며, 불일치 시 거부됩니다.

## 에이전트 도구 샌드박스

### `run_command()` 위험 플래그 차단

고정 allowlist(`pwd`, `ls`, `find`, `cat`, `head`, `tail`, `wc`, `rg`)와 정화된
환경만 사용합니다. Python/Node/npm/npx/sed 같은 일반 인터프리터, 실행 파일 경로,
절대 경로·`..` traversal·workspace 밖 symlink, `rg --pre`, `find -exec/-delete`
등은 실행 전에 거부합니다.

### `edit_file()` 유일성 검증

- `old_string`이 파일에 정확히 한 번만 존재해야 성공
- `replace_all=true`로 전체 치환 허용
- 워크스페이스 외부 경로 접근 차단 (`../../../etc/passwd` 등)

### `grep()` 이진 디렉토리 제외

`node_modules`, `.git`, `venv`, `dist`, `__pycache__` 자동 제외

## 감사 로그

- 어드민 세션 핸드오프 이벤트 로깅
- 평문 비밀번호 마이그레이션 이벤트: `password_migrated_from_plaintext`
- `server.log` 파일에 모든 요청 기록

## Telegram 및 권한 알림

- Telegram은 `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS`에 지정된 chat의 메시지와
  callback query만 처리하며, 허용 전에 chat을 등록하지 않습니다.
- 봇의 로컬 API 호출에는 `LATTICEAI_SERVER_SESSION_TOKEN` 전용 bearer가
  필요합니다. 봇은 `sessions.json`을 스캔하지 않습니다.
- 권한 요청 알림은 전체 승인 토큰 대신 8자 hint만 포함합니다. 선택적인
  `LATTICEAI_PERMISSION_UI_URL`은 사람의 검토 페이지로 연결하지만 토큰을 URL에
  넣지 않습니다.
- permission queue는 atomic write와 POSIX `0600`을 사용합니다.

## 텔레메트리

**없음.** 모든 데이터는 로컬에만 저장됩니다. 외부 서버로 어떠한 사용 데이터도 전송되지 않습니다.

예외: 사용자가 직접 설정한 클라우드 API(OpenAI, Groq 등)로의 프롬프트 전송은 해당 제공업체의 정책을 따릅니다.

## 퍼블릭 배포 체크리스트

- [ ] `LATTICEAI_MODE=public`
- [ ] 초대 온보딩을 사용할 때만 `LATTICEAI_INVITE_GATE_ENABLED=true`와 비공개
  `LATTICEAI_INVITE_CODE` 설정(미설정 시 생성되는 설치별 secret 영구 보관)
- [ ] HTTPS 리버스 프록시 (nginx/Caddy)
- [ ] `LATTICEAI_ENABLE_GRAPH=false` (필요 시)
- [ ] `/data` 영구 볼륨 마운트
- [ ] `LATTICEAI_ALLOW_LOCAL_MODELS=false`
- [ ] 방화벽에서 4825 포트 직접 노출 차단 (리버스 프록시 통해서만)
- [ ] Telegram 활성화 시 `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS`와
  `LATTICEAI_SERVER_SESSION_TOKEN` 설정

자세한 내용: [public-deploy.md](public-deploy.md)
