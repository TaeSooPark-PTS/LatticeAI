# Skill: summarize_document

## 메타데이터
- **버전**: 0.1.0
- **카테고리**: document
- **위험도**: low
- **필요 권한**: local_read

## 설명
텍스트 파일(.txt, .md, .pdf, .docx)을 읽어 핵심 내용을 요약하고, 섹션별 요점과 키워드를 추출한다.

## 거버넌스

`risk.json` 참고. 요약: `risk=read`, `destructive=false`, `shell=false`, `network=false`, `auto_approve=true`, `sandbox=home`, `rollback=none`

## 트리거 조건

호출해야 하는 상황:
- 사용자가 "이 문서 요약해줘", "핵심만 뽑아줘", "이 파일 내용이 뭐야?"라고 요청할 때
- 에이전트가 Discover 단계에서 긴 문서의 구조를 빠르게 파악해야 할 때
- 여러 문서를 비교하기 전 각 문서의 핵심 파악이 필요할 때

호출하면 **안** 되는 상황:
- 문서를 수정해야 할 때 → `edit_file` 사용
- CSV/Excel 데이터 분석 → `data_analysis` 사용
- 코드 파일 분석 → `code_review` 사용

## Side Effects

| 항목 | 내용 |
|------|------|
| 파일 변경 | 없음 |
| 생성 파일 | 없음 |
| 프로세스 | 없음 |
| 네트워크 | 없음 |

## Rollback

없음. 읽기 전용 작업.

## 입력 스키마
`schema.json` 참고.

## 출력 스키마
`schema.json` 참고.

## 실행 조건
- LLM 모델이 로드되어 있어야 함
- 파일이 존재해야 하며 .txt/.md/.pdf/.docx 형식이어야 함
- 파일 크기 20 MB 이하

## 예제
`examples.md` 참고.

## 실패 처리
| 에러 코드 | 원인 | 처리 방법 |
|-----------|------|-----------|
| `INVALID_INPUT` | path 누락 | 파일 경로 입력 |
| `FILE_NOT_FOUND` | 경로에 파일 없음 | 경로 확인 |
| `UNSUPPORTED_FORMAT` | 지원하지 않는 형식 | .txt/.md/.pdf/.docx로 변환 후 재시도 |
| `SIZE_LIMIT` | 20 MB 초과 | 파일 분할 후 재시도 |
| `MODEL_NOT_LOADED` | LLM 미로드 | `/model` 명령으로 모델 선택 |

## 테스트 케이스
```python
# tests/unit/test_tools.py::test_summarize_document_md
# tests/unit/test_tools.py::test_summarize_document_unsupported
```
