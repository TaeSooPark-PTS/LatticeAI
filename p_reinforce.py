"""
P-Reinforce Knowledge Gardener
Raw 데이터를 자동으로 분석해서 구조화된 마크다운 위키로 정리
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

BRAIN_DIR = Path.home() / ".connect-ai-brain"

STRUCTURE = {
    "10_Wiki":   "검증된 지식, 개념 설명, 레퍼런스",
    "00_Raw":    "정제되지 않은 원시 데이터, 아이디어 메모",
    "20_Skills": "재사용 가능한 코드 스니펫, 프롬프트, 워크플로",
    "30_Projects": "프로젝트별 컨텍스트, 진행 상황",
    "40_Log":    "날짜별 작업 로그",
}


class PReinforceGardener:
    def __init__(self):
        self._ensure_structure()

    def _ensure_structure(self):
        for folder in STRUCTURE:
            (BRAIN_DIR / folder).mkdir(parents=True, exist_ok=True)
        # 인덱스 파일
        index_path = BRAIN_DIR / "INDEX.md"
        if not index_path.exists():
            index_path.write_text(self._render_index())

    def _render_index(self) -> str:
        lines = ["# 🧠 Connect AI Brain — P-Reinforce Index\n"]
        lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
        for folder, desc in STRUCTURE.items():
            lines.append(f"## [{folder}](./{folder}/)\n_{desc}_\n")
        return "\n".join(lines)

    # ── Classify ──────────────────────────────────────────────────────────────

    def _classify(self, text: str) -> str:
        """간단한 규칙 기반 분류 (LLM 없이도 동작)"""
        text_lower = text.lower()

        code_signals = ["def ", "class ", "import ", "```", "function ", "const ", "let ", "var "]
        if any(s in text for s in code_signals):
            return "20_Skills"

        wiki_signals = ["개념", "원리", "이란", "what is", "how does", "definition", "explanation"]
        if any(s in text_lower for s in wiki_signals):
            return "10_Wiki"

        project_signals = ["project", "프로젝트", "todo", "task", "작업", "기능", "feature"]
        if any(s in text_lower for s in project_signals):
            return "30_Projects"

        return "00_Raw"

    # ── File Naming ───────────────────────────────────────────────────────────

    def _make_filename(self, text: str, folder: str) -> str:
        # 첫 줄을 제목으로
        first_line = text.strip().split("\n")[0][:60]
        # 파일명 안전하게
        safe = re.sub(r"[^\w\s-]", "", first_line).strip()
        safe = re.sub(r"\s+", "_", safe)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{timestamp}_{safe or 'note'}.md"

    # ── Process ───────────────────────────────────────────────────────────────

    async def process(self, raw_data: str, category: Optional[str] = None) -> dict:
        folder = category if category in STRUCTURE else self._classify(raw_data)
        filename = self._make_filename(raw_data, folder)
        filepath = BRAIN_DIR / folder / filename

        # 마크다운 래핑
        content = self._wrap_markdown(raw_data, folder)
        filepath.write_text(content, encoding="utf-8")

        # 오늘 로그에도 기록
        self._append_log(raw_data[:200], folder, filename)

        return {
            "status": "saved",
            "folder": folder,
            "filename": filename,
            "path": str(filepath),
            "classified_as": folder,
            "description": STRUCTURE[folder],
        }

    def _wrap_markdown(self, raw: str, folder: str) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        first_line = raw.strip().split("\n")[0][:80]
        lines = [
            f"# {first_line}",
            f"\n> 📁 `{folder}` | 🕐 {now} | Connect AI MLX\n",
            "---\n",
            raw,
            "\n\n---",
            f"*Auto-organized by P-Reinforce Gardener*",
        ]
        return "\n".join(lines)

    def _append_log(self, preview: str, folder: str, filename: str):
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = BRAIN_DIR / "40_Log" / f"{today}.md"
        entry = f"\n- [{datetime.now().strftime('%H:%M')}] → `{folder}/{filename}`\n  > {preview[:100]}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            if log_path.stat().st_size == 0 if log_path.exists() else True:
                f.write(f"# 📅 Log — {today}\n")
            f.write(entry)

    # ── Tree ──────────────────────────────────────────────────────────────────

    def get_relevant_context(self, query: str, limit: int = 3) -> str:
        """질문과 관련된 지식을 검색하여 컨텍스트 문자열을 반환합니다."""
        results = []
        # 모든 마크다운 파일 탐색 (INDEX.md 및 Log 제외)
        for file_path in BRAIN_DIR.rglob("*.md"):
            if file_path.name == "INDEX.md" or "40_Log" in str(file_path):
                continue
            
            try:
                content = file_path.read_text(encoding="utf-8")
                # 간단한 키워드 매칭 검색
                keywords = [k for k in re.split(r'\s+', query) if len(k) > 1]
                if any(k.lower() in content.lower() for k in keywords):
                    results.append(f"--- Document: {file_path.name} ---\n{content[:800]}")
                    if len(results) >= limit:
                        break
            except:
                continue
        
        return "\n\n".join(results)
