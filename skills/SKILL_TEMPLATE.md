# Skill: <name>

## 메타데이터
- **버전**: 0.1.0
- **카테고리**: coding | data | document | web | system | analysis
- **위험도**: low | medium | high
- **필요 권한**: none | local_read | local_write | exec | network | admin

## 설명

한 줄 설명.

## 거버넌스

`risk.json` 참고. 요약: `risk=read|write|exec|destructive`, `destructive=false`, `shell=false`, `network=false`, `auto_approve=true`, `sandbox=workspace|home|system`, `rollback=none|backup|git`

## 트리거 조건

호출해야 하는 상황:
- (예) 사용자가 "이 파일 고쳐줘", "수정해줘"라고 말할 때
- (예) 에이전트가 Implement 단계에서 코드 변경이 필요할 때

호출하면 **안** 되는 상황:
- (예) 파일 내용 확인만 필요할 때 → `read_file` 사용
- (예) 디렉토리 목록 조회 → `list_dir` 사용

## Side Effects

| 항목 | 내용 |
|------|------|
| 파일 변경 | 없음 |
| 생성 파일 | 없음 |
| 프로세스 | 없음 |
| 네트워크 | 없음 |

## Rollback

| 항목 | 내용 |
|------|------|
| 가능 여부 | none / git |
| 방법 | (없음 / `git checkout <file>`) |
| 주의사항 | git 미초기화 시 rollback 불가 |

## 입력 스키마

`schema.json` → `input` 블록 참고.

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `field1` | string | ✅ | ... |
| `field2` | integer | ❌ | 기본값: 10 |

## 출력 스키마

`schema.json` → `output` 블록 참고.

성공:
```json
{ "success": true, "result": { ... } }
```
실패:
```json
{ "success": false, "error": "ERROR_CODE", "message": "..." }
```

## 실행 조건

- 사전 조건 (예: 모델 로드 필요, 파일 존재 여부)

## 실패 처리

`schema.json` → `evals` 블록 참고.

| 에러 코드 | 원인 | 처리 방법 |
|-----------|------|-----------|
| `INVALID_INPUT` | 필수 필드 누락 | 입력 검증 후 재시도 |
| `PERMISSION_DENIED` | 권한 없음 | 관리자에게 문의 |
| `TIMEOUT` | 실행 시간 초과 | 작업 분할 후 재시도 |

## 예제

`examples.md` 참고.

## 테스트 케이스

```python
# tests/unit/test_tools.py::test_<name>_*
# tests/integration/test_api.py::test_agent_<name>_*
```
