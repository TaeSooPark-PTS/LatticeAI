# weekly_review — Examples

## 1. 자료가 있는 한 주 (success)

**Input**
```json
{
  "path": "reviews/2026-W30.md",
  "week": "2026-W30",
  "content": "# 2026-W30 주간 회고\n\n## 이번 주 들어온 것\n- 1분기 예산 1,200만원 확정 (출처: docs/budget.md)\n- 주간회의 노트 (출처: notes/2026-07-27-weekly.md)\n\n## 남은 할 일\n- [ ] 2분기 계획 이사회 승인\n\n## 다음 주\n- 이사회 일정 확정하기\n"
}
```
**Output**
```json
{ "success": true, "path": "reviews/2026-W30.md", "bytes": 241 }
```

모든 항목에 출처가 붙어 있다 — Brain에 기록이 있는 것만 썼다는 증거다.

## 2. 자료가 없는 한 주 (success, 빈 주를 채우지 않음)

**Input**
```json
{
  "path": "reviews/2026-W31.md",
  "content": "# 2026-W31 주간 회고\n\n이번 주 새로 들어온 자료가 없습니다.\n"
}
```
**Output**
```json
{ "success": true, "path": "reviews/2026-W31.md", "bytes": 62 }
```

없는 성과를 지어내는 대신 없다고 적는다.

## 3. 프로젝트 세션과 함께

`project_id`를 주면 그 프로젝트가 만든 파일과 남은 TODO, 마지막 검증 결과가
"남은 할 일" 섹션의 근거가 된다 (`GET /api/projects/{id}`).
