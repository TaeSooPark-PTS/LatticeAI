# code_review — Examples

## 1. Security review of a snippet (success)

**Input**
```json
{ "target": "def foo(x):\n  return eval(x)\n", "focus": ["security"] }
```
**Output**
```json
{
  "success": true,
  "result": {
    "summary": "1개의 심각한 보안 이슈 발견",
    "issues": [{ "severity": "high", "line": 2, "category": "security", "message": "eval()에 사용자 입력이 직접 전달됨 — 임의 코드 실행 가능" }],
    "score": 40
  }
}
```

## 2. File review (success)

**Input**
```json
{ "target": "~/project/server.py", "focus": ["bug","performance"], "max_lines": 200 }
```
**Output**
```json
{
  "success": true,
  "result": {
    "summary": "전반적으로 양호. 1개의 중간 수준 버그 발견.",
    "issues": [{ "severity": "medium", "line": 87, "category": "bug", "message": "race condition: shared dict에 lock 없이 동시 접근 가능" }],
    "score": 75
  }
}
```

## 3. Empty target (failure)

**Input**
```json
{ "target": "" }
```
**Output**
```json
{ "success": false, "error": "INVALID_INPUT", "message": "target is required" }
```

## 4. Model not loaded (failure)

**Input**
```json
{ "target": "x = 1\n" }
```
**Output**
```json
{ "success": false, "error": "MODEL_NOT_LOADED", "message": "LLM이 로드되지 않았습니다. /model 명령으로 모델을 선택하세요." }
```
