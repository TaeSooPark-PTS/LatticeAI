# Skill: <name>

## 메타데이터
- **버전**: 0.1.0
- **카테고리**: [coding | data | document | web | system | analysis]
- **위험도**: [low | medium | high]  <!-- low=읽기, medium=쓰기, high=실행/삭제 -->
- **필요 권한**: [none | local_read | local_write | exec | admin]

## 설명
한 줄 설명.

## 입력 스키마
```json
{
  "required": ["field1"],
  "optional": ["field2"],
  "properties": {
    "field1": { "type": "string", "description": "..." },
    "field2": { "type": "integer", "default": 10 }
  }
}
```

## 출력 스키마
```json
{
  "success": true,
  "result": "...",
  "artifacts": []
}
```

## 실행 조건
- 사전 조건 (예: 모델 로드 필요, 파일 존재 여부)
- 후처리 조건

## 예제

### 성공 케이스
**입력**: `{ "field1": "예시값" }`  
**출력**: `{ "success": true, "result": "..." }`

### 실패 케이스
**입력**: `{ "field1": "" }`  
**출력**: `{ "success": false, "error": "field1 is required" }`

## 실패 처리
| 에러 코드 | 원인 | 처리 방법 |
|-----------|------|-----------|
| `INVALID_INPUT` | 필수 필드 누락 | 입력 검증 후 재시도 |
| `PERMISSION_DENIED` | 권한 없음 | 관리자에게 문의 |
| `TIMEOUT` | 실행 시간 초과 | 작업 분할 후 재시도 |

## 테스트 케이스
```python
# tests/skills/test_<name>.py 참조
```
