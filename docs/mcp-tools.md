# MCP 도구 카탈로그 (v9.6.0)

Lattice AI는 MCP(Model Context Protocol) 서버로 동작하여 Claude Desktop, Cursor 등에서 직접 도구를 사용할 수 있습니다.

## 연결 설정

The MCP surface is **streamable HTTP JSON-RPC** at `POST /mcp` on the
loopback gateway (default `http://localhost:4825`). There is no separate
stdio or SSE transport.

`claude_desktop_config.json` or Cursor MCP settings:

```json
{
  "mcpServers": {
    "lattice-ai": {
      "url": "http://localhost:4825/mcp"
    }
  }
}
```

Auth is the same session gate as every other product route:

- Loopback install with authentication off → the anonymous local owner (this
  is what a localhost client such as Claude Desktop uses).
- Authentication on → send the `session_token` cookie or
  `Authorization: Bearer <token>`.

Supported JSON-RPC methods: `initialize`, `notifications/initialized`,
`tools/list`, `tools/call`. Unknown methods return `-32601`. Notifications
have no result body (`202`).

Every native tool call goes through the existing tool governor. A governance
refusal is a JSON-RPC error (`-32001`), not a successful tool result.

## 도구 목록

MCP exposes a **curated, read-oriented** subset of Lattice native tools,
plus each installed skill as a prompt asset. Writes, shell, desktop control,
and remote-install tools are **not** on this surface — use the REST `/tools/*`
routes (with their own approval gates) for those.

### Native (governed)

| 도구 | 설명 | 위험도 |
|------|------|--------|
| `list_dir` | 워크스페이스 디렉터리 목록 (sandbox) | 낮음 |
| `read_file` | 워크스페이스 UTF-8 파일 읽기 (offset/limit) | 낮음 |
| `workspace_tree` | 재귀 트리 (깊이 제한) | 낮음 |
| `grep` | 워크스페이스 정규식 검색 | 낮음 |
| `knowledge_search` | 지식 정원 검색 (workspace scope, 승인 정책 적용) | 낮음 |
| `knowledge_tree` | 지식 정원 파일 목록 (동일 정책) | 낮음 |
| `git_status` | 워크스페이스 안 read-only `git status` | 낮음 |

### Skills (prompt assets)

Installed skills from the skills directory appear as `skill.<name>`
(for example `skill.code_review`). `tools/list` serves the parsed
`schema.json` input schema. `tools/call` returns the `SKILL.md` body plus
the input echo — skills are not executables.

Typical names when the repo `skills/` tree is installed:

- `skill.code_review`
- `skill.data_analysis`
- `skill.file_edit`
- `skill.meeting_notes`
- `skill.summarize_document`
- `skill.web_search`
- `skill.weekly_review`

## REST API

The same governed subset is also reachable at `POST /mcp/call`
(`{"action": "...", "args": {}}`) and the existing `/tools/*` routes.

```bash
# MCP JSON-RPC initialize
curl -s -X POST http://localhost:4825/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'

# MCP tools/call
curl -s -b "session_token=<token>" -X POST http://localhost:4825/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_dir","arguments":{"path":"."}}}'

# REST sibling (same dispatch)
curl -s -b "session_token=<token>" -X POST http://localhost:4825/mcp/call \
  -H "Content-Type: application/json" \
  -d '{"action":"list_dir","args":{"path":"."}}'

# Full write/exec surface (not on MCP)
curl -s -b "session_token=<token>" -X POST http://localhost:4825/tools/edit_file \
  -H "Content-Type: application/json" \
  -d '{"path": "server.py", "old_string": "old code", "new_string": "new code"}'
```

`run_command` remains a REST-only admin tool. It allows `pwd`, `ls`, `find`,
`cat`, `head`, `tail`, `wc`, `rg` only. Python/Node/npm/npx/sed, shell
operators, executable paths, absolute paths, `..` traversal, workspace-outside
symlinks, `rg --pre`, and `find -exec/-delete` are refused.

## 도구 카탈로그 조회

```bash
curl -b "session_token=<token>" http://localhost:4825/mcp/tools
```

`GET /mcp/tools` lists Lattice's native tool catalog (names, descriptions,
governance). `tools/list` on `POST /mcp` is the MCP-shaped list with JSON
Schemas for the curated subset plus skills.

`POST /mcp/install` enables a skill or plugin Lattice can actually flip in
the workspace registry. Remote npm/pip/connector entries return
`{"status":"manual_required", ...}` instead of a blank 404.

The catalogs require authentication and do not return the absolute
`AGENT_ROOT`. MCP/plugin dispatch cannot bypass user, workspace, capability,
or approval policy. Knowledge-garden reads use explicit user/workspace scope.
