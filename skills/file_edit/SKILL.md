# Skill: file_edit

## 메타데이터
- **버전**: 0.1.0
- **카테고리**: system
- **위험도**: medium
- **필요 권한**: local_write

## 설명
로컬 파일을 읽고 특정 범위를 편집한 뒤 저장한다. 원본 백업을 `.bak` 파일로 생성한다.

## 거버넌스

`policies/policy.md` 참고. 요약: `risk=write`, `destructive=false`, `shell=false`, `network=false`, `auto_approve=false`, `sandbox=workspace`, `rollback=git`

## 트리거 조건

호출해야 하는 상황:
- 에이전트가 Implement 단계에서 코드/설정 파일을 변경해야 할 때
- 사용자가 "이 부분 고쳐줘", "이 라인 바꿔줘", "수정해줘"라고 요청할 때
- `read_file`로 내용 확인 후 특정 문자열을 교체해야 할 때

호출하면 **안** 되는 상황:
- 파일 내용 확인만 필요할 때 → `read_file` 사용
- 파일 전체를 처음부터 새로 쓸 때 → `write_file` 사용
- 바이너리 파일(이미지, ZIP 등) 수정 시도 → 불가, `BINARY_FILE` 에러

## Side Effects

| 항목 | 내용 |
|------|------|
| 파일 변경 | 대상 파일의 내용이 영구 변경됨 |
| 생성 파일 | 없음 (`backup=false` 기본) |
| 프로세스 | 없음 |
| 네트워크 | 없음 |

## Rollback

| 항목 | 내용 |
|------|------|
| 가능 여부 | `git` — git이 초기화된 워크스페이스에서 복구 가능 |
| 방법 | `git diff <file>` 확인 후 `git checkout <file>` |
| 주의사항 | git 미초기화 시 rollback 불가 (`.bak` 파일 없음, backup=false 기본) |

## 입력 스키마
```json
{
  "required": ["path", "new_content"],
  "optional": ["start_line", "end_line", "backup"],
  "properties": {
    "path":        { "type": "string",  "description": "절대 경로 또는 ~/로 시작하는 경로" },
    "new_content": { "type": "string",  "description": "교체할 새 내용" },
    "start_line":  { "type": "integer", "description": "편집 시작 줄 번호 (1-indexed). 없으면 전체 교체" },
    "end_line":    { "type": "integer", "description": "편집 종료 줄 번호 (포함). 없으면 start_line 단일 줄" },
    "backup":      { "type": "boolean", "default": true, "description": "false면 .bak 파일 생성 생략" }
  }
}
```

## 출력 스키마
```json
{
  "success": true,
  "result": {
    "path": "...",
    "lines_changed": 3,
    "backup_path": "....bak"
  }
}
```

## 실행 조건
- 대상 파일이 존재해야 함
- BINARY_EXTS(png, jpg, zip 등) 파일은 거부
- 파일 크기 10 MB 이하

## 예제

### 성공 케이스
**입력**: `{ "path": "~/project/config.py", "new_content": "DEBUG = False\n", "start_line": 5, "end_line": 5 }`
**출력**: `{ "success": true, "result": { "path": "...", "lines_changed": 1, "backup_path": "...config.py.bak" } }`

### 실패 케이스
**입력**: `{ "path": "~/photo.png", "new_content": "..." }`
**출력**: `{ "success": false, "error": "BINARY_FILE", "message": "Binary files cannot be edited as text" }`

## 실패 처리
| 에러 코드 | 원인 | 처리 방법 |
|-----------|------|-----------|
| `INVALID_INPUT` | path 또는 new_content 누락 | 필드 확인 후 재시도 |
| `FILE_NOT_FOUND` | 경로에 파일 없음 | 경로 확인 |
| `BINARY_FILE` | 바이너리 파일 편집 시도 | 텍스트 파일만 지원 |
| `PERMISSION_DENIED` | 파일 쓰기 권한 없음 | 관리자 권한 확인 |
| `SIZE_LIMIT` | 파일 10 MB 초과 | 파일 분할 후 재시도 |

## 테스트 케이스
```python
# tests/unit/test_tools.py::test_file_edit_full_replace
# tests/unit/test_tools.py::test_file_edit_line_range
# tests/unit/test_tools.py::test_file_edit_binary_rejected
```
