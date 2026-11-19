[![English](https://img.shields.io/badge/English-Switch-475569?style=flat-square)](../mcp-tools.md) [![한국어](https://img.shields.io/badge/한국어-현재_언어-2563eb?style=flat-square)](./mcp-tools.md)

# MCP 도구 카탈로그 (v9.6.0)

Lattice AI는 MCP(Model Context Protocol) 서버로 동작하여 Claude Desktop, Cursor 등에서 거버넌스가 적용된 도구를 직접 사용할 수 있습니다.

## 연결 설정

MCP 표면은 루프백 게이트웨이(기본값 `http://localhost:4825`)의 `POST /mcp`에서 **스트리밍 가능 HTTP JSON-RPC**로 제공됩니다. 별도의 stdio나 SSE 전송 계층은 없습니다.

`claude_desktop_config.json` 또는 Cursor MCP 설정:

```json
{
  "mcpServers": {
    "lattice-ai": {
      "url": "http://localhost:4825/mcp"
    }
  }
}
```

인증은 다른 모든 제품 라우트와 동일한 세션 게이트를 따릅니다.

- 인증이 꺼진 루프백 설치 → 익명 로컬 소유자로 처리(Claude Desktop 같은 로컬호스트 클라이언트가 사용하는 방식).
- 인증이 켜진 경우 → `session_token` 쿠키 또는 `Authorization: Bearer <token>` 헤더 전송.

지원되는 JSON-RPC 메서드: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`. 알 수 없는 메서드는 `-32601`을 반환하며, 알림(notification)은 응답 본문 없이 `202`를 반환합니다.

모든 네이티브 도구 호출은 기존 도구 거버너(tool governor)를 거칩니다. 거버넌스 거부는 성공 응답이 아니라 JSON-RPC 오류(`-32001`)로 반환됩니다.

**v12.0.0부터 `POST /mcp`는 OpenAPI 제품 계약에 포함됩니다.** 게이트웨이 마운트 테이블에 **단일 JSON-RPC 엔벨로프 오퍼레이션**으로 선언되어 하나의 경로, 하나의 메서드, 하나의 요청/응답 스키마를 가지며, 위 메서드 어휘는 URL이 아니라 본문에 담겨 전달됩니다. v11.9.0까지는 설계상 계약 밖에 있었으나, 계약 밖에 두는 것이 문서화하지 않아도 안전하다는 뜻은 아니므로 이제 다른 모든 제품 라우트와 마찬가지로 스펙에 자동 생성됩니다. 알아두면 좋은 두 가지 결과는 다음과 같습니다.

- 이 라우트는 **네이티브로 마운트**되어 프로세스 내부에서 직접 처리되며 Python 워커로 프록시되지 않습니다. 워커의 커밋된 허용 목록에는 이 라우트가 없고, 게이트웨이는 허용 목록에 없는 요청을 절대 전달하지 않습니다.
- 에이전트 루프도 동일한 거버넌스를 거칩니다. 실행의 통합 도구 카탈로그는 네이티브 도구 및 스킬과 함께 `mcp.<tool>`을 노출하며, 이를 디스패치할 때 이 엔드포인트와 동일한 거버넌스 검사를 수행합니다. 이미 네이티브로 관리되는 도구 이름은 더 엄격한 네이티브 경로를 타므로 `mcp.` 접두사가 같은 도구를 우회 호출하는 통로가 될 수 없습니다.

## 도구 목록

MCP는 Lattice 네이티브 도구 가운데 **선별된 읽기 중심** 부분 집합과 설치된 각 스킬(프롬프트 자산)을 노출합니다. 쓰기, 셸 실행, 데스크톱 제어, 원격 설치 도구는 이 표면에 포함되지 **않으며**, 해당 기능은 별도의 승인 게이트가 있는 REST `/tools/*` 라우트를 사용해야 합니다.

### 네이티브 도구 (거버넌스 적용)

| 도구 | 설명 | 위험도 |
|------|------|--------|
| `list_dir` | 워크스페이스 디렉터리 목록 (샌드박스) | 낮음 |
| `read_file` | 워크스페이스 UTF-8 파일 읽기 (`offset`/`limit` 지원) | 낮음 |
| `workspace_tree` | 재귀 워크스페이스 트리 (깊이 제한) | 낮음 |
| `grep` | 워크스페이스 정규식 검색 | 낮음 |
| `knowledge_search` | 지식 정원 검색 (워크스페이스 스코프, 승인 정책 적용) | 낮음 |
| `knowledge_tree` | 지식 정원 파일 목록 (동일 정책 적용) | 낮음 |
| `git_status` | 워크스페이스 내 읽기 전용 `git status` | 낮음 |

### 스킬 (프롬프트 자산)

스킬 디렉터리에 설치된 스킬은 `skill.<name>` 형태(예: `skill.code_review`)로 나타납니다. `tools/list`는 파싱된 `schema.json` 입력 스키마를 제공하고, `tools/call`은 `SKILL.md` 본문과 입력 에코를 반환합니다. 스킬은 실행 파일이 아닙니다.

저장소의 `skills/` 트리가 설치된 경우 대표적인 이름은 다음과 같습니다.

- `skill.code_review`
- `skill.data_analysis`
- `skill.file_edit`
- `skill.meeting_notes`
- `skill.summarize_document`
- `skill.web_search`
- `skill.weekly_review`

## REST API

동일한 거버넌스 부분 집합은 `POST /mcp/call`(`{"action": "...", "args": {}}`)과 기존 `/tools/*` 라우트에서도 호출할 수 있습니다.

```bash
# MCP JSON-RPC 초기화
curl -s -X POST http://localhost:4825/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'

# MCP tools/call 호출
curl -s -b "session_token=<token>" -X POST http://localhost:4825/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_dir","arguments":{"path":"."}}}'

# 동일 디스패치를 사용하는 REST 형제 엔드포인트
curl -s -b "session_token=<token>" -X POST http://localhost:4825/mcp/call \
  -H "Content-Type: application/json" \
  -d '{"action":"list_dir","args":{"path":"."}}'

# 전체 쓰기/실행 표면 (MCP에서는 비노출)
curl -s -b "session_token=<token>" -X POST http://localhost:4825/tools/edit_file \
  -H "Content-Type: application/json" \
  -d '{"path": "server.py", "old_string": "old code", "new_string": "new code"}'
```

`run_command`는 REST 전용 관리자 도구로 유지됩니다. `pwd`, `ls`, `find`, `cat`, `head`, `tail`, `wc`, `rg`만 허용하며, Python/Node/npm/npx/sed, 셸 연산자, 실행 파일 경로, 절대 경로, `..` 경로 탈출, 워크스페이스 외부 심볼릭 링크, `rg --pre`, `find -exec/-delete`는 모두 거부됩니다.

## 도구 카탈로그 조회

```bash
curl -b "session_token=<token>" http://localhost:4825/mcp/tools
```

`GET /mcp/tools`는 Lattice 네이티브 도구 카탈로그(이름, 설명, 거버넌스 정보)를 나열합니다. `POST /mcp`의 `tools/list`는 선별된 부분 집합과 스킬에 대한 JSON Schema를 MCP 형식으로 반환합니다.

`POST /mcp/install`은 Lattice가 워크스페이스 레지스트리에서 실제로 켜고 끌 수 있는 스킬이나 플러그인을 활성화합니다. 원격 npm/pip/커넥터 항목은 빈 404 대신 `{"status":"manual_required", ...}`를 반환합니다.

카탈로그 조회에는 인증이 필요하며 절대 `AGENT_ROOT` 경로는 반환하지 않습니다. MCP/플러그인 디스패치는 사용자, 워크스페이스, 기능, 승인 정책을 우회할 수 없으며 지식 정원 읽기는 명시적인 사용자/워크스페이스 스코프를 사용합니다.
