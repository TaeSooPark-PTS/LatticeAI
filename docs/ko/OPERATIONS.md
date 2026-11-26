[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../OPERATIONS.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./OPERATIONS.md)

# Lattice AI — 운영 가이드 (v14.2.0)

> **상태: 정본 (14.2.0)** — 현재 릴리스와 동기화됩니다. 아래 저장소 구조는
> 초기 노드별 Markdown 그래프가 아니라 SQLite 라이브 Brain 스토어와 워크스페이스
> 스코핑을 반영합니다.

## 1. 데이터 파일 위치

| 파일 | 용도 |
|------|------|
| `~/.ltcai/users.json` | 사용자 계정 (scrypt 해시 저장) |
| `~/.ltcai/knowledge_graph.sqlite` | Brain 라이브 스토어 — Knowledge Graph 노드/엣지/청크, 대화 히스토리, 프로비넌스 (`workspace_id` 스코프) |
| `~/.ltcai/knowledge_graph_blobs/` | 업로드 원본/blob 저장소 |
| `~/.ltcai/audit.json` | 감사 로그 (agent 실행, 사용자 변경 등) |
| `~/.ltcai/chat_history.json` | 레거시 대화 히스토리 — 최초 1회 SQLite로 idempotent 임포트 후 미사용 |
| `~/ltcai-agent/` | Agent workspace 기본 베이스 경로 — 인증 시 서버 측 `IsolatedRootSpec` 으로 사용자/테넌트/워크스페이스별 격리 |
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

인앱 백업(`.latticebrain` / `VACUUM INTO` 스냅샷)은 암호화·무결성 검증이 완료된
이식 경로이며(**RFC 7914 scrypt 또는 X25519 + HKDF-SHA256 + SHA256-CTR/HMAC-SHA256**,
`export` / `inspect` / `verify` / `restore` dry-run·`confirm` / `import` 지원),
**v12.0.0부터 즉시 반영됩니다 — 복원 뒤 재시작이 필요 없습니다.** 스토어가
세대(generation) 에폭을 들고 있어서, 복원이 에폭을 올리면 이전 세대에서
열린 풀 커넥션은 다음 체크아웃에서 낡은 것으로 판정되어 닫히고, 그다음
읽기는 복원된 바이트를 봅니다.

SQLite 라이브 DB 옆에 생성되는 일시 WAL/SHM 파일(`*.db-wal`, `*.db-shm`) 및
`/scratchpad/` 검증 출력은 `.gitignore` 로 제외되어 저장소에 커밋되지 않습니다.

삭제된 파일 정리 및 단건 노드 삭제도 일관되게 통합되었습니다:
`POST /api/ingestion/folder/prune` 는 `confirm` 없이 dry-run을 반환하고,
`confirm` 시 `delete_document_tree` 를 통해 서브트리를 제거합니다. 단건
`GraphWriter::delete_node` 역시 동일한 `delete_node_subgraph` 경로를 사용해
양방향 엣지(`PART_OF` 포함), 청크, 벡터, 고아 `Source` 노드, BM25 통계,
프로비넌스를 함께 정리합니다. 어느 경로도 디스크의 원본 파일을 지우지 않습니다.

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

| `event_type` | 발생 시점 |
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
import json, os
events = json.load(open(os.path.expanduser('~/.ltcai/audit.json')))
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

### 6.4 14.2.0 업그레이드
- `LATTICEAI_EMBEDDING_PROVIDER` 기본값이 `auto`입니다. 이전 동작을 유지하려면 `hash`로 설정하세요. 임베더가 바뀌면 `reindex_required`가 보고됩니다.
- 선택 모델: `pip install "ltcai[rag]"` 후 `google/embeddinggemma-2`와 `BAAI/bge-reranker-v2-m3`를 미리 캐시하세요.
- 기존 문서는 다음 동기화 때 한 번 다시 임베딩됩니다. 파괴적 마이그레이션은 없습니다.
- 새 검색 설정 전체는 [14.2.0 릴리스 노트](./releases/RELEASE_NOTES_v14.2.0.md#환경-변수)를 참고하세요.

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

> **참고**: Telegram 브리지와 외부 SSO OIDC 로그인/콜백 플로우는 Rust 호스트
> 전환과 함께 제거되어 `LATTICEAI_TELEGRAM_*`는 더 이상 읽히지 않습니다.
> 설정 표면은 남아 있고 패스워드 로그인은 네이티브(`lattice-auth`)입니다.

## 8. 사용자 데이터 격리 및 임베딩/모델 운영

- 대화 히스토리는 `user_email` 필드로 필터링됩니다.
- 인증이 필수인 환경에서는 대화 파생 그래프 노드도 소유자 기준으로 필터링합니다. 문서와 업로드는 워크스페이스 범위로만 구분됩니다.
- **사용자별 `AGENT_ROOT` 격리**: 에이전트 작업 디렉터리는 더 이상 단일 공유
  디렉터리가 아닙니다. Rust 호스트/에이전트 런타임(`IsolatedRootSpec`,
  `resolve_isolated_agent_root`, `LoopRoutesState::isolated`)이 서버 측에서
  `(user_id, tenant_id, workspace_id)` 단위로 격리된 `AGENT_ROOT`를 산출하며,
  공유 워크스페이스는 멤버십 인가(`workspace_access_denied`)를 통과하지 못하면
  fail-closed 로 거부됩니다.
- Knowledge Graph는 `workspace_id`로 스코프됩니다. 알 수 없는 스코프의 노드는
  기본 비공개(fail-closed)이며, 레거시 글로벌 읽기는 명시적 호환성 옵트인이
  있을 때만 허용됩니다. 지식 노드는 `KnowledgeLifecycleState`(`active`,
  `superseded`, `deprecated`, `disputed`, `pending_verification`)를 추적하며
  기본 검색에서는 `superseded`/`deprecated` 노드를 제외합니다.
- **임베딩 계약 및 인제스트 상태**: Python(`latticeai.core.embedding_providers`)과
  Rust(`lattice_core::embeddings`)는 동일한 `EmbeddingIndexContract`
  (`provider`, `model_id`, `model_revision`, `dimension`, `normalization`,
  `passage_mode`, `query_mode`, `index_version`, `grade`, `semantic`)를 공유합니다.
  계약이 불일치하거나 인덱스 재구축이 필요하면 `IngestStatus::ReindexRequired`
  (`complete` / `partial` / `pending` / `failed` / `reindex_required`)로 보고됩니다.
- **Hugging Face 모델 카탈로그 및 캐시 검증**:
  - `.venv/bin/python scripts/verify_hf_model_registry.py` 는 가중치 다운로드
    없이 Hub 메타데이터/파일 크기를 정적 검증합니다.
  - 운영/오프라인 환경에서는 `hf_hub_download` / `from_pretrained` 호출 경로에
    대한 커밋 `revision` 고정 및 해시 검증 도입 전까지 로컬 사전 다운로드 캐시와
    `EmbeddingIndexContract.model_revision` 기록을 점검하세요.
  - PostgreSQL / pgvector 마이그레이션 경로는 `Planned` (사전 점검/드라이런)
    상태이며 공식 라이브 스토어는 SQLite 입니다.

## 9. 서버 시작 / 종료

```bash
# 시작
LTCAI

# 환경변수와 함께 시작
LATTICEAI_SECRET_KEY=xxx LATTICEAI_ENABLE_GRAPH=true LTCAI

# 컨테이너 등 외부 인터페이스 바인딩 (LATTICEAI_REQUIRE_AUTH=true 필수, 미설정 시 기동 거부)
LATTICEAI_REQUIRE_AUTH=true lattice-host --bind 0.0.0.0 --port 4825

# 종료
Ctrl+C  또는  kill <pid>
```

`lattice-host` 는 기본적으로 루프백(`127.0.0.1:4825`)에만 바인딩하며, 비-루프백 주소(`0.0.0.0` 등)는 `LATTICEAI_REQUIRE_AUTH=true` 가 활성화된 경우에만 허용됩니다. 프로덕션 Tauri 데스크톱 빌드는 `PortPolicy::PinnedProduction`으로 포트 `4825`를 고정하여 다른 프로세스가 이미 포트를 점유한 경우 조용히 폴백하지 않고 즉시 실패(fail-fast)합니다. Rust와 Python은 모두 오프셋 없는 UTC(`YYYY-MM-DDTHH:MM:SS`) 타임스탬프를 사용합니다. 종료 시에는 진행 중인 HTTP 요청과 워커 프로세스를 안전하게 정리한 뒤 종료합니다.

## 10. 릴리스 증거 캡처 순서 (레이아웃·UI 변경 후)

`scripts/capture_release_evidence.mjs` 의 `assertNotOverwritingTaggedRelease()` 는
`package.json` 버전과 동일한 git 태그(`vX.Y.Z`)가 이미 있으면 **즉시 exit 1** 한다.
레이아웃/UI를 바꾼 뒤 스크린샷을 다시 찍을 때는 반드시 아래 순서를 지킨다.

1. **버전 범프 먼저** — `node scripts/run_python.mjs scripts/bump_version.py`
   (또는 동등한 package/pyproject/extension 버전 일괄 상향). 캡처 **전에** 한다.
2. `npm run build:assets` — 캡처는 빌드된 `static/app` 을 본다.
3. `npm run release:evidence` — `output/release/v<새버전>/` 에만 기록한다.
   캡처 → 오래된 버전 정리 → **픽셀 델타 검사**까지 한 번에 돈다.
4. 결속 검사 — `SCREENSHOT_INDEX.md` 의 `asset-manifest.sha256` 과
   `mock-server.sha256` 이 각각 `static/app/asset-manifest.json`,
   그리고 목 API 표면 전체(`tests/visual/mock_server.cjs` +
   `tests/visual/mock_server/*.cjs`) 와 일치해야 `npm run lint` 가 통과한다.
   v11.3.0 부터 지문이 진입 파일 하나가 아니라 라우트 모듈까지 덮으므로,
   v11.3.0 이전에 찍은 증거는 재캡처 전까지 불일치로 보고된다
   (지문 계산은 `scripts/lib/mock_server_fingerprint.mjs` 한 곳에만 있다).

레이아웃을 바꿨다고 보고했는데 배치는 그대로인 경우를 잡기 위해 v10.7.0 부터
`scripts/check_screenshot_pixel_delta.py` 가 12개 캡처를 직전 버전과 대조한다
(`npm run check:screenshot-delta` 로 단독 실행). 정적 화면은 1.5%, 애니메이션이
있는 화면은 8% 이상 픽셀이 달라져야 통과한다. 문구만 고친 화면은 여기서 실패로
잡히므로, 실패했다면 캡처가 아니라 레이아웃을 다시 본다. 직전 버전 디렉터리가
정리되어 없으면 기준선을 꺼낼 태그를 체크아웃한 뒤 다시 돌린다.

이미 태그가 찍힌 버전의 `output/release/vX.Y.Z/` 를 WIP UI로 덮으면 그 릴리스
증거가 영구히 거짓이 된다. 미리보기만 필요하면
`LTCAI_RELEASE_EVIDENCE_DIR=/tmp/lattice-preview npm run release:evidence` 로
게시 경로 밖을 쓴다. 태그된 버전의 원본 증거가 오염됐다면 태그 시점 트리에서
해당 디렉터리를 복원한 뒤, 새 버전 디렉터리에 다시 캡처한다.
