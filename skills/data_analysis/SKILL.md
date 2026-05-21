# Skill: data_analysis

## 메타데이터
- **버전**: 0.1.0
- **카테고리**: data
- **위험도**: low
- **필요 권한**: local_read

## 설명
CSV/Excel/JSON 파일을 읽어 기초 통계, 컬럼 요약, 이상치 탐지를 수행하고 인사이트를 제공한다.

## 거버넌스

`policies/policy.md` 참고. 요약: `risk=read`, `destructive=false`, `shell=false`, `network=false`, `auto_approve=true`, `sandbox=home`, `rollback=none`

## 트리거 조건

호출해야 하는 상황:
- 사용자가 "이 CSV 분석해줘", "이 데이터 통계 내줘", "이상치 찾아줘"라고 요청할 때
- 에이전트가 Discover 단계에서 데이터 파일의 구조를 파악해야 할 때
- 상관관계, 추세, 분포를 파악해야 할 때

호출하면 **안** 되는 상황:
- 파일을 수정해야 할 때 → `edit_file` / `write_file` 사용
- .csv가 아닌 텍스트 파일 내용 검색 → `grep` 사용

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
```json
{
  "required": ["path"],
  "optional": ["columns", "analysis_type", "max_rows"],
  "properties": {
    "path":          { "type": "string", "description": "분석할 파일 절대 경로 (.csv, .xlsx, .json)" },
    "columns":       { "type": "array",  "items": { "type": "string" }, "description": "분석할 컬럼 목록. 미입력시 전체" },
    "analysis_type": { "type": "array",  "items": { "type": "string", "enum": ["summary", "outlier", "correlation", "trend"] }, "default": ["summary"], "description": "수행할 분석 유형" },
    "max_rows":      { "type": "integer", "default": 10000, "description": "처리할 최대 행 수" }
  }
}
```

## 출력 스키마
```json
{
  "success": true,
  "result": {
    "shape": [100, 5],
    "columns": ["col1", "col2"],
    "summary": { "col1": { "mean": 42.0, "std": 3.1, "min": 10, "max": 99 } },
    "outliers": { "col1": [99, 10] },
    "insights": "..."
  }
}
```

## 실행 조건
- pandas, openpyxl 패키지 설치 필요 (requirements.txt 포함)
- 파일이 존재해야 하며 .csv/.xlsx/.json 형식이어야 함

## 예제

### 성공 케이스
**입력**: `{ "path": "~/data/sales.csv", "analysis_type": ["summary", "outlier"] }`
**출력**:
```json
{
  "success": true,
  "result": {
    "shape": [500, 4],
    "columns": ["date", "revenue", "units", "region"],
    "summary": { "revenue": { "mean": 15000.0, "std": 4200.0, "min": 200, "max": 89000 } },
    "outliers": { "revenue": [89000] },
    "insights": "revenue 컬럼에서 1개의 이상치(89000) 발견. 평균 대비 17.6 표준편차."
  }
}
```

### 실패 케이스
**입력**: `{ "path": "~/data/photo.png" }`
**출력**: `{ "success": false, "error": "UNSUPPORTED_FORMAT", "message": "Supported formats: csv, xlsx, json" }`

## 실패 처리
| 에러 코드 | 원인 | 처리 방법 |
|-----------|------|-----------|
| `INVALID_INPUT` | path 누락 | 파일 경로 입력 |
| `FILE_NOT_FOUND` | 경로에 파일 없음 | 경로 확인 |
| `UNSUPPORTED_FORMAT` | csv/xlsx/json 이외 형식 | 지원 형식으로 변환 후 재시도 |
| `PARSE_ERROR` | 파일 파싱 실패 | 파일 인코딩/형식 확인 |
| `SIZE_LIMIT` | max_rows 초과 | max_rows 값 조정 |

## 테스트 케이스
```python
# tests/unit/test_tools.py::test_data_analysis_csv_summary
# tests/unit/test_tools.py::test_data_analysis_unsupported_format
```
