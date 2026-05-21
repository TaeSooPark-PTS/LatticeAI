# Skill: web_search

## 메타데이터
- **버전**: 0.1.0
- **카테고리**: web
- **위험도**: low
- **필요 권한**: none

## 설명
외부 검색 엔진(DuckDuckGo/Brave)을 통해 웹 검색을 수행하고 상위 결과를 반환한다.

## 거버넌스

`policies/policy.md` 참고. 요약: `risk=read`, `destructive=false`, `shell=false`, `network=true`, `auto_approve=true`, `sandbox=system`, `rollback=none`

## 트리거 조건

호출해야 하는 상황:
- 사용자가 "검색해줘", "찾아봐줘", "최신 정보 알려줘"라고 요청할 때
- 에이전트가 Discover 단계에서 외부 문서/API/라이브러리 정보를 수집해야 할 때
- LLM의 학습 데이터 이후 최신 정보(라이브러리 버전, 뉴스 등)가 필요할 때

호출하면 **안** 되는 상황:
- 로컬 파일 내용 검색 시 → `grep` 사용
- LLM이 이미 알고 있는 일반 지식 질문 시

## Side Effects

| 항목 | 내용 |
|------|------|
| 파일 변경 | 없음 |
| 생성 파일 | 없음 |
| 프로세스 | 없음 |
| 네트워크 | 외부 검색 API로 검색어 전송 (DuckDuckGo/Brave) |

## Rollback

없음. 읽기 전용 네트워크 요청.

## 입력 스키마
```json
{
  "required": ["query"],
  "optional": ["num_results", "lang"],
  "properties": {
    "query":       { "type": "string",  "description": "검색어" },
    "num_results": { "type": "integer", "default": 5, "description": "반환할 결과 수 (1-20)" },
    "lang":        { "type": "string",  "default": "ko-KR", "description": "검색 언어 로케일" }
  }
}
```

## 출력 스키마
```json
{
  "success": true,
  "result": {
    "query": "...",
    "results": [
      { "title": "...", "url": "...", "snippet": "..." }
    ]
  }
}
```

## 실행 조건
- 네트워크 연결 필요
- 외부 API 키 불필요 (DuckDuckGo instant answer API 사용)

## 예제

### 성공 케이스
**입력**: `{ "query": "FastAPI 비동기 처리", "num_results": 3 }`
**출력**:
```json
{
  "success": true,
  "result": {
    "query": "FastAPI 비동기 처리",
    "results": [
      { "title": "FastAPI - Async", "url": "https://fastapi.tiangolo.com/async/", "snippet": "..." }
    ]
  }
}
```

### 실패 케이스
**입력**: `{ "query": "" }`
**출력**: `{ "success": false, "error": "INVALID_INPUT", "message": "query is required" }`

## 실패 처리
| 에러 코드 | 원인 | 처리 방법 |
|-----------|------|-----------|
| `INVALID_INPUT` | query 비어 있음 | 검색어 입력 후 재시도 |
| `NETWORK_ERROR` | 외부 API 연결 실패 | 잠시 후 재시도 |
| `TIMEOUT` | 5초 초과 | 검색어 단순화 후 재시도 |

## 테스트 케이스
```python
# tests/unit/test_tools.py::test_web_search_returns_results
# tests/integration/test_api.py::test_agent_web_search
```
