# Lattice AI — Operations Guide (v9.1.0)

## 1. 데이터 파일 위치

| 파일 | 용도 |
|------|------|
| `~/.ltcai/users.json` | 사용자 계정 (scrypt 해시 저장) |
| `~/.ltcai/history.json` | 대화 히스토리 |
| `~/.ltcai/audit.json` | 감사 로그 (agent 실행, 사용자 변경 등) |
| `~/.ltcai/brain/` | Knowledge Graph 노드 (Markdown) |
| `~/ltcai-agent/` | Agent workspace (agent가 생성한 파일) |

## 2. 백업 및 복구

### 2.1 전체 백업
```bash
tar -czf ltcai-backup-$(date +%Y%m%d).tar.gz ~/.ltcai ~/ltcai-agent
```

### 2.2 복구
```bash
tar -xzf ltcai-backup-YYYYMMDD.tar.gz -C ~
```

### 2.3 히스토리만 초기화
```bash
rm ~/.ltcai/history.json
```

### 2.4 감사 로그 보관 (90일 보관 예시)
```bash
# crontab -e 에 추가
0 2 * * * find ~/.ltcai -name "audit*.json" -mtime +90 -delete
```

## 3. 로그 관리

서버 로그는 stdout/stderr로 출력됩니다. systemd 환경에서는 journald가 관리합니다.

### 3.1 로그 확인
```bash
# systemd 사용 시
journalctl -u ltcai -f

# 직접 실행 시 파일로 redirect
LTCAI > /var/log/ltcai.log 2>&1
```

### 3.2 로그 보관
`logrotate` 예시 (`/etc/logrotate.d/ltcai`):
```
/var/log/ltcai.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
}
```

## 4. 감사 로그 이벤트 유형

| event_type | 발생 시점 |
|------------|-----------|
| `login` | 로그인 성공/실패 |
| `user_update` | 관리자가 사용자 수정 |
| `user_delete` | 관리자가 사용자 삭제 |
| `history_delete` | 대화 히스토리 삭제 |
| `agent_exec` | Agent가 medium/high risk 도구 실행 |
| `agent_blocked` | Agent가 시스템 경로 쓰기 시도하여 차단됨 |
| `model_load` | 모델 로드 |
| `model_switch` | 모델 전환 |

### 4.1 감사 로그 조회
```bash
python3 -c "
import json, sys
events = json.load(open('~/.ltcai/audit.json'.replace('~', __import__('os').path.expanduser('~'))))
for e in events[-20:]:
    print(e['timestamp'], e['event_type'], e.get('user_email',''))
"
```

## 5. 사용자 관리

### 5.1 첫 번째 사용자 (admin) 생성
서버 첫 실행 시 `/register` API 또는 웹 UI를 통해 등록합니다.  
최초 등록 사용자는 자동으로 `admin` 역할이 부여됩니다.

### 5.2 사용자 목록 확인 (admin 전용)
```
GET /admin/users
Cookie: session_id=<admin-session>
```

### 5.3 비밀번호 정책
- 최소 8자
- scrypt 해시로 저장
- 평문은 저장되지 않음

## 6. 업그레이드 마이그레이션

### 6.1 npm 패키지 업그레이드
```bash
npm install -g ltcai@latest
```

### 6.2 PyPI 패키지 업그레이드
```bash
pip install --upgrade ltcai
```

### 6.3 데이터 마이그레이션 주의사항
- `users.json` 스키마 변경 시: 서버가 자동으로 누락 필드를 기본값으로 보완합니다.
- `history.json` 스키마 변경 시: 하위 호환성 유지. 구버전 레코드는 무시됩니다.
- `brain/` Knowledge Graph: Markdown 파일 기반이므로 별도 마이그레이션 없음.

## 7. 공개 서버 체크리스트

공개 인터넷에 노출할 경우 반드시 확인:

- [ ] `LATTICEAI_SECRET_KEY` 환경변수 설정 (32자 이상 랜덤 문자열)
- [ ] `LATTICEAI_MODE=public` 및 HTTPS에서 Secure cookie 동작 확인
- [ ] 초대 게이트 사용 시 랜덤 invite code 또는 생성된 설치별 secret 영구 보관
- [ ] `LATTICEAI_ENABLE_GRAPH=false` (불필요 시 Graph 비활성화)
- [ ] `HTTPS` 종단 프록시 설정 (nginx / Caddy)
- [ ] `CSRF_TRUSTED_ORIGINS` 화이트리스트 설정
- [ ] `ALLOWED_COMMANDS` 검토 — python3/node 등 불필요한 실행 도구 제거
- [ ] 방화벽으로 4825 포트를 프록시만 접근 허용
- [ ] Telegram 활성화 시 `LATTICEAI_TELEGRAM_ALLOWED_CHAT_IDS`와
  `LATTICEAI_SERVER_SESSION_TOKEN` 설정

## 8. 사용자 데이터 격리

- 대화 히스토리는 `user_email` 필드로 필터링됩니다.
- Agent workspace (`~/ltcai-agent/`)는 현재 단일 공유 디렉토리입니다.  
  멀티 유저 환경에서는 `~/ltcai-agent/<email>/` 구조로 분리를 권장합니다.
- Knowledge Graph (`brain/`) 역시 단일 공유 공간입니다.

## 9. 서버 시작 / 종료

```bash
# 시작
LTCAI

# 환경변수와 함께 시작
LATTICEAI_SECRET_KEY=xxx LATTICEAI_ENABLE_GRAPH=true LTCAI

# 종료
Ctrl+C  또는  kill <pid>
```

서버는 종료 시 진행 중인 HTTP 요청을 완료한 후 종료됩니다 (graceful shutdown — uvicorn 기본 동작).
