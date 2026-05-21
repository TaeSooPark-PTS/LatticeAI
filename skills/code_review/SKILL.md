# Skill: code_review

## 메타데이터
- **버전**: 0.1.0
- **카테고리**: coding
- **위험도**: low
- **필요 권한**: local_read

## 설명
파일 또는 코드 스니펫을 LLM에 전달해 버그, 보안 이슈, 성능, 스타일을 리뷰한다.

## 거버넌스

`policies/policy.md` 참고. 요약: `risk=read`, `destructive=false`, `shell=false`, `network=false(local)/true(cloud)`, `auto_approve=true`, `sandbox=workspace`, `rollback=none`

## 트리거 조건

호출해야 하는 상황:
- 사용자가 "이 코드 리뷰해줘", "보안 검토해줘", "버그 있어?"라고 요청할 때
- 에이전트가 Verify 단계에서 작성한 코드를 검증해야 할 때
- PR 제출 전 코드 품질 확인이 필요할 때

호출하면 **안** 되는 상황:
- 코드를 수정해야 할 때 → `edit_file` 사용
- 파일 내용만 확인할 때 → `read_file` 사용

## Side Effects

| 항목 | 내용 |
|------|------|
| 파일 변경 | 없음 |
| 생성 파일 | 없음 |
| 프로세스 | 없음 |
| 네트워크 | 클라우드 LLM 사용 시 코드 내용이 외부 API로 전송됨 |

## Rollback

없음. 읽기 전용 작업.

## 입력 스키마
```json
{
  "required": ["target"],
  "optional": ["focus", "lang", "max_lines"],
  "properties": {
    "target":    { "type": "string", "description": "절대 파일 경로 또는 코드 스니펫 문자열" },
    "focus":     { "type": "array",  "items": { "type": "string", "enum": ["bug", "security", "performance", "style"] }, "default": ["bug", "security"], "description": "리뷰 초점 목록" },
    "lang":      { "type": "string", "description": "언어 힌트 (python, js, go …). 미입력시 자동 감지" },
    "max_lines": { "type": "integer", "default": 500, "description": "분석할 최대 줄 수" }
  }
}
```

## 출력 스키마
```json
{
  "success": true,
  "result": {
    "summary": "...",
    "issues": [
      { "severity": "high|medium|low", "line": 42, "category": "security", "message": "..." }
    ],
    "score": 85
  }
}
```

## 실행 조건
- LLM 모델이 로드되어 있어야 함 (`/mode` 응답의 `model` 필드 비어있지 않음)
- 파일 대상인 경우 파일이 존재해야 함

## 예제

### 성공 케이스
**입력**: `{ "target": "~/project/server.py", "focus": ["security"] }`
**출력**:
```json
{
  "success": true,
  "result": {
    "summary": "1개의 심각한 보안 이슈 발견",
    "issues": [{ "severity": "high", "line": 102, "category": "security", "message": "SQL 쿼리에 사용자 입력이 직접 삽입됨" }],
    "score": 60
  }
}
```

### 실패 케이스
**입력**: `{ "target": "" }`
**출력**: `{ "success": false, "error": "INVALID_INPUT", "message": "target is required" }`

## 실패 처리
| 에러 코드 | 원인 | 처리 방법 |
|-----------|------|-----------|
| `INVALID_INPUT` | target 비어 있음 | 파일 경로 또는 코드 입력 |
| `FILE_NOT_FOUND` | 파일 경로 존재하지 않음 | 경로 확인 |
| `MODEL_NOT_LOADED` | LLM 미로드 | `/model` 명령으로 모델 선택 |
| `SIZE_LIMIT` | max_lines 초과 파일 | max_lines 값 조정 또는 파일 분할 |

## 테스트 케이스
```python
# tests/unit/test_tools.py::test_code_review_snippet
# tests/integration/test_api.py::test_agent_code_review_file
```
