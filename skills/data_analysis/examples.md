# data_analysis — Examples

## 1. CSV summary + outlier detection (success)

**Input**
```json
{ "path": "~/data/sales.csv", "analysis_type": ["summary", "outlier"] }
```
**Output**
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

## 2. Correlation analysis (success)

**Input**
```json
{ "path": "~/data/metrics.csv", "columns": ["cpu", "latency"], "analysis_type": ["correlation"] }
```
**Output**
```json
{
  "success": true,
  "result": {
    "shape": [1000, 2],
    "columns": ["cpu", "latency"],
    "summary": { "cpu": { "mean": 45.2 }, "latency": { "mean": 120.5 } },
    "insights": "cpu와 latency 간 강한 양의 상관관계 (r=0.87) 확인."
  }
}
```

## 3. Unsupported format (failure)

**Input**
```json
{ "path": "~/data/photo.png" }
```
**Output**
```json
{ "success": false, "error": "UNSUPPORTED_FORMAT", "message": "Supported formats: csv, xlsx, json" }
```

## 4. File not found (failure)

**Input**
```json
{ "path": "~/data/missing.csv" }
```
**Output**
```json
{ "success": false, "error": "FILE_NOT_FOUND", "message": "No such file: /home/user/data/missing.csv" }
```
