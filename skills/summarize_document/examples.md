# summarize_document — Examples

## 1. Markdown file summary (success)

**Input**
```json
{ "path": "~/project/README.md", "style": "bullet" }
```
**Output**
```json
{
  "success": true,
  "result": {
    "title": "README.md",
    "summary": "• Lattice AI는 Apple Silicon 기반 로컬 AI 에이전트\n• FastAPI 서버 + VS Code 익스텐션 + Telegram bot 구조\n• MLX 및 클라우드 모델(OpenAI/Groq) 지원",
    "keywords": ["Lattice AI", "MLX", "FastAPI", "VS Code", "Telegram", "local LLM"],
    "sections": [
      { "heading": "Installation", "summary": "pip install ltcai 후 ltcai start로 실행" },
      { "heading": "Features", "summary": "채팅, 에이전트 모드, 파일 편집, 웹 검색 등 지원" }
    ],
    "word_count": 1240
  }
}
```

## 2. PDF summary with focus section (success)

**Input**
```json
{ "path": "~/docs/report.pdf", "style": "paragraph", "focus_sections": ["결론", "권고사항"], "max_length": 300 }
```
**Output**
```json
{
  "success": true,
  "result": {
    "title": "report.pdf",
    "summary": "보고서의 결론: 시스템 성능이 전분기 대비 23% 향상되었으며, 추가 최적화를 위해 캐시 레이어 도입이 권고됩니다.",
    "keywords": ["성능", "최적화", "캐시", "권고"],
    "word_count": 8500
  }
}
```

## 3. Unsupported format (failure)

**Input**
```json
{ "path": "~/data/sales.csv" }
```
**Output**
```json
{ "success": false, "error": "UNSUPPORTED_FORMAT", "message": "Supported formats: txt, md, pdf, docx. Use data_analysis for CSV files." }
```

## 4. File not found (failure)

**Input**
```json
{ "path": "~/docs/nonexistent.md" }
```
**Output**
```json
{ "success": false, "error": "FILE_NOT_FOUND", "message": "No such file: /home/user/docs/nonexistent.md" }
```
