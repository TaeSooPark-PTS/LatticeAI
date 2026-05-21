# web_search — Examples

## 1. Basic search (success)

**Input**
```json
{ "query": "FastAPI 비동기 처리", "num_results": 3 }
```
**Output**
```json
{
  "success": true,
  "result": {
    "query": "FastAPI 비동기 처리",
    "results": [
      { "title": "FastAPI - Async", "url": "https://fastapi.tiangolo.com/async/", "snippet": "FastAPI는 Python의 asyncio를 완벽히 지원합니다..." }
    ]
  }
}
```

## 2. Search with language (success)

**Input**
```json
{ "query": "Python type hints best practices", "num_results": 5, "lang": "en-US" }
```
**Output**
```json
{
  "success": true,
  "result": {
    "query": "Python type hints best practices",
    "results": [
      { "title": "PEP 484 – Type Hints", "url": "https://peps.python.org/pep-0484/", "snippet": "This PEP introduces a standard syntax for type annotations..." }
    ]
  }
}
```

## 3. Empty query (failure)

**Input**
```json
{ "query": "" }
```
**Output**
```json
{ "success": false, "error": "INVALID_INPUT", "message": "query is required" }
```

## 4. Network error (failure)

**Input**
```json
{ "query": "offline test" }
```
**Output**
```json
{ "success": false, "error": "NETWORK_ERROR", "message": "외부 검색 API에 연결할 수 없습니다. 잠시 후 재시도하세요." }
```
