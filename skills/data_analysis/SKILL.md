# Skill: data_analysis

## 메타데이터
- **버전**: 0.1.0
- **카테고리**: data
- **위험도**: low
- **필요 권한**: local_read

## 설명
CSV/Excel/JSON 파일을 읽어 기초 통계, 컬럼 요약, 이상치 탐지를 수행하고 인사이트를 제공한다.

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
