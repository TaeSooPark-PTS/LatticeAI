# MCP 도구 카탈로그

Lattice AI는 MCP(Model Context Protocol) 서버로 동작하여 Claude Desktop, Cursor 등에서 직접 도구를 사용할 수 있습니다.

## 연결 설정

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

## 도구 목록

### 파일 시스템

| 도구 | 설명 | 위험도 |
|------|------|--------|
| `read_file` | 파일 읽기 (라인 번호, offset/limit 지원) | 낮음 |
| `edit_file` | 정밀 diff 편집 (`old_string` 유일성 검증) | 중간 |
| `list_dir` | 디렉토리 목록 | 낮음 |
| `grep` | 정규식 검색, glob 필터, context_lines | 낮음 |

### 실행

| 도구 | 설명 | 위험도 |
|------|------|--------|
| `run_command` | 셸 명령 실행 (위험 패턴 차단) | 높음 |
| `run_terminal_command` | 터미널 명령 (별칭) | 높음 |

### 작업 관리

| 도구 | 설명 | 위험도 |
|------|------|--------|
| `todo_write` | TODO 항목 생성/업데이트 | 낮음 |
| `todo_read` | TODO 목록 조회 | 낮음 |

### 시스템

| 도구 | 설명 | 위험도 |
|------|------|--------|
| `computer_screenshot` | 화면 캡처 | 낮음 |
| `computer_open_app` | 앱 실행 | 중간 |
| `computer_open_url` | URL 열기 | 낮음 |
| `network_status` | IP, Wi-Fi 정보 | 낮음 |

### 문서

| 도구 | 설명 | 위험도 |
|------|------|--------|
| `pdf_to_text` | PDF → 텍스트 변환 | 낮음 |
| `pdf_pages` | PDF 페이지 수 조회 | 낮음 |
| `read_docx` | Word 문서 읽기 | 낮음 |
| `read_xlsx` | Excel 파일 읽기 | 낮음 |
| `read_pptx` | PowerPoint 읽기 | 낮음 |

### 지식 정원 (P-Reinforce)

| 도구 | 설명 | 위험도 |
|------|------|--------|
| `garden_save` | 지식 정원에 저장 | 낮음 |
| `garden_tree` | 지식 트리 조회 | 낮음 |
| `garden_read` | 정원 파일 읽기 | 낮음 |

## REST API 직접 호출

MCP 대신 REST API로도 동일한 도구를 호출할 수 있습니다:

```bash
# read_file
curl -b "session=<token>" \
  "http://localhost:4825/tools/read_file?path=server.py"

# edit_file
curl -b "session=<token>" -X POST \
  http://localhost:4825/tools/edit_file \
  -H "Content-Type: application/json" \
  -d '{"path": "server.py", "old_string": "old code", "new_string": "new code"}'

# grep
curl -b "session=<token>" \
  "http://localhost:4825/tools/grep?pattern=def%20main&glob=*.py"

# run_command
curl -b "session=<token>" -X POST \
  http://localhost:4825/tools/run_command \
  -H "Content-Type: application/json" \
  -d '{"command": "python -m pytest tests/ -v"}'
```

## 도구 카탈로그 조회

```bash
curl http://localhost:4825/mcp/tools
```

응답:
```json
{
  "tools": [
    {
      "name": "edit_file",
      "description": "정밀 diff 편집. old_string이 파일에 유일해야만 성공.",
      "risk": "medium",
      "parameters": { ... }
    },
    ...
  ]
}
```
