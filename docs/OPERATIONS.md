# Lattice AI — Operations Guide (v10.3.0)

> **Status: canonical** — kept in sync with the current release. Storage layout
> below reflects the SQLite live Brain store and workspace scoping, not the
> earlier Markdown-per-node graph.

## 1. 데이터 파일 위치

| 파일 | 용도 |
|------|------|
| `~/.ltcai/users.json` | 사용자 계정 (scrypt 해시 저장) |
| `~/.ltcai/knowledge_graph.sqlite` | Brain 라이브 스토어 — Knowledge Graph 노드/엣지/청크, 대화 히스토리, 프로비넌스 (workspace_id 스코프) |
| `~/.ltcai/knowledge_graph_blobs/` | 업로드 원본/blob 저장소 |
| `~/.ltcai/audit.json` | 감사 로그 (agent 실행, 사용자 변경 등) |
| `~/.ltcai/chat_history.json` | 레거시 대화 히스토리 — 최초 1회 SQLite로 idempotent 임포트 후 미사용 |
| `~/ltcai-agent/` | Agent workspace (agent가 생성한 파일) |
| `~/.ltcai/agent_runs/` | 승인 대기 중인 에이전트 런 (재시작 생존, 토큰은 해시로만 저장) |
| `~/.ltcai/project_sessions/` | 프로젝트 세션 (v9.9.6) — 프로젝트가 만든 파일, 남은 할 일, 마지막 검증 결과 |

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
- 대화 히스토리: SQLite 라이브 스토어에 저장됩니다. 레거시 `chat_history.json`은
  최초 실행 시 1회 idempotent 임포트된 뒤 사용되지 않습니다.
- `knowledge_graph.sqlite` Knowledge Graph: SQLite 스키마 마이그레이션은 부팅 시
  적용되며 읽기 호환성/롤백/동등성 테스트로 보호됩니다.

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
- Knowledge Graph는 `workspace_id`로 스코프됩니다. 알 수 없는 스코프의 노드는
  기본 비공개(fail-closed)이며, 레거시 글로벌 읽기는 명시적 호환성 옵트인이
  있을 때만 허용됩니다.

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
