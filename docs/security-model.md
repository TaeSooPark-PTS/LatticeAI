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

- UUID 토큰, `~/.ltcai/sessions.json` 파일 저장
- TTL: 24시간 + sliding refresh (활동 시 자동 연장, 15분 단위 디스크 쓰기)
- 쿠키: `HttpOnly; SameSite=Lax; Path=/`
- 서버 재시작 후에도 유지 (파일 기반)

### SSO (선택적)

- Entra ID / Okta OIDC (`OIDC_DISCOVERY_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`)
- 콜백 후 내부 세션 토큰으로 변환
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

## 에이전트 도구 샌드박스

### `run_command()` 위험 플래그 차단

다음 패턴이 포함된 명령 실행 거부:
- `rm -rf`, `sudo`, `chmod 777`, `curl | bash`, `wget | sh`
- `> /dev/sda`, `dd if=`, `mkfs`

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

## 텔레메트리

**없음.** 모든 데이터는 로컬에만 저장됩니다. 외부 서버로 어떠한 사용 데이터도 전송되지 않습니다.

예외: 사용자가 직접 설정한 클라우드 API(OpenAI, Groq 등)로의 프롬프트 전송은 해당 제공업체의 정책을 따릅니다.

## 퍼블릭 배포 체크리스트

- [ ] `LATTICEAI_MODE=public`
- [ ] `LATTICEAI_INVITE_CODE` 비공개 값 설정
- [ ] HTTPS 리버스 프록시 (nginx/Caddy)
- [ ] `LATTICEAI_ENABLE_GRAPH=false` (필요 시)
- [ ] `/data` 영구 볼륨 마운트
- [ ] `LATTICEAI_ALLOW_LOCAL_MODELS=false`
- [ ] 방화벽에서 4825 포트 직접 노출 차단 (리버스 프록시 통해서만)

자세한 내용: [public-deploy.md](public-deploy.md)
