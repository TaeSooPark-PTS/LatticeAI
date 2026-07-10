"""
P-Reinforce Knowledge Gardener — notes capture with a brain-backed memory.

v4 (T4.3 garden absorption): the markdown vault is no longer a second brain.
The vault stays as the user-owned, Obsidian-compatible *mirror* (capability
preserved), but the Knowledge Graph is authoritative: notes created through
the API are ingested through the unified pipeline (provenance + hooks), the
existing vault is imported idempotently, and chat context comes from brain
queries instead of an O(n) vault scan per message.
"""

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

BRAIN_DIR = Path(
    os.getenv("LATTICEAI_OBSIDIAN_VAULT_DIR")
    or os.getenv("LATTICEAI_BRAIN_DIR")
    or Path.home() / ".ltcai-brain"
)

STRUCTURE = {
    "10_Wiki":   "검증된 지식, 개념 설명, 레퍼런스",
    "00_Raw":    "정제되지 않은 원시 데이터, 아이디어 메모",
    "20_Skills": "재사용 가능한 코드 스니펫, 프롬프트, 워크플로",
    "30_Projects": "프로젝트별 컨텍스트, 진행 상황",
    "40_Log":    "날짜별 작업 로그",
}


class PReinforceGardener:
    def __init__(self, ingestion_pipeline: Any = None, knowledge_graph: Any = None):
        self._pipeline = ingestion_pipeline
        self._kg = knowledge_graph
        self._ensure_structure()

    def _ensure_structure(self):
        for folder in STRUCTURE:
            (BRAIN_DIR / folder).mkdir(parents=True, exist_ok=True)
        # 인덱스 파일
        index_path = BRAIN_DIR / "INDEX.md"
        if not index_path.exists():
            index_path.write_text(self._render_index())

    def _render_index(self) -> str:
        lines = ["# 🧠 Lattice AI Brain — P-Reinforce Index\n"]
        lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n")
        lines.append("\nThis folder is an Obsidian-compatible Markdown vault.\n")
        lines.append("\nThe Knowledge Graph is the authoritative store; this vault is the\nuser-owned markdown mirror of garden notes.\n")
        for folder, desc in STRUCTURE.items():
            lines.append(f"## [{folder}](./{folder}/)\n_{desc}_\n")
        lines.append("## Connector Status\n")
        lines.append(f"- OCR engine: `{'tesseract' if shutil.which('tesseract') else 'not installed'}`\n")
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

        # 마크다운 미러 (사용자 소유 Obsidian 호환 아티팩트)
        content = self._wrap_markdown(raw_data, folder)
        filepath.write_text(content, encoding="utf-8")

        # 오늘 로그에도 기록
        self._append_log(raw_data[:200], folder, filename)

        result = {
            "status": "saved",
            "folder": folder,
            "filename": filename,
            "path": str(filepath),
            "classified_as": folder,
            "description": STRUCTURE[folder],
        }
        # 두뇌(Knowledge Graph)가 정식 저장소: 통합 수집 파이프라인으로 ingest.
        result.update(self._ingest_note(raw_data, source_uri=str(filepath), folder=folder))
        return result

    def _ingest_note(self, text: str, *, source_uri: str, folder: str, title: Optional[str] = None) -> dict:
        if self._pipeline is None:
            return {"graph": "unavailable", "graph_detail": "ingestion pipeline not wired"}
        try:
            from lattice_brain.ingestion import IngestionItem

            ingest = self._pipeline.ingest(
                IngestionItem(
                    source_type="note",
                    title=title or text.strip().split("\n")[0][:80],
                    text=text,
                    source_uri=source_uri,
                    metadata={"garden_folder": folder, "pipeline": "p-reinforce"},
                )
            )
            if ingest.status != "ok":
                return {"graph": ingest.status, "graph_detail": ingest.detail}
            return {
                "graph": "ok",
                "graph_node_id": ingest.node_id,
                "provenance_id": ingest.provenance_id,
                "duplicate": ingest.duplicate,
            }
        except Exception as exc:
            logging.warning("garden note ingest failed: %s", exc)
            return {"graph": "failed", "graph_detail": str(exc)}

    def import_vault(self) -> dict:
        """Idempotent import of every existing vault note into the brain.

        Content-hash dedup in the store makes re-runs safe; vault files are
        never modified or deleted. INDEX.md and the daily logs are skipped.
        """
        if self._pipeline is None:
            return {"status": "unavailable", "imported": 0}
        imported = duplicates = failed = 0
        for file_path in sorted(BRAIN_DIR.rglob("*.md")):
            if file_path.name == "INDEX.md" or "40_Log" in file_path.parts:
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except Exception:
                failed += 1
                continue
            folder = file_path.parent.name if file_path.parent != BRAIN_DIR else "00_Raw"
            outcome = self._ingest_note(
                text, source_uri=str(file_path), folder=folder, title=file_path.stem
            )
            if outcome.get("graph") == "ok":
                if outcome.get("duplicate"):
                    duplicates += 1
                else:
                    imported += 1
            else:
                failed += 1
        if imported:
            logging.info("garden: imported %d vault notes into the brain (%d already known)", imported, duplicates)
        return {"status": "ok", "imported": imported, "duplicates": duplicates, "failed": failed}

    def _wrap_markdown(self, raw: str, folder: str) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        first_line = raw.strip().split("\n")[0][:80]
        lines = [
            f"# {first_line}",
            f"\n> 📁 `{folder}` | 🕐 {now} | Lattice AI MLX\n",
            "---\n",
            raw,
            "\n\n---",
            "*Auto-organized by P-Reinforce Gardener*",
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

    def get_tree(self) -> dict:
        """지식 정원 파일트리 (마크다운 미러 기준)."""
        folders = []
        for folder, desc in STRUCTURE.items():
            folder_path = BRAIN_DIR / folder
            files = []
            if folder_path.exists():
                for file_path in sorted(folder_path.glob("*.md")):
                    try:
                        stat = file_path.stat()
                        files.append({
                            "name": file_path.name,
                            "size_bytes": stat.st_size,
                            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                        })
                    except OSError:
                        continue
            folders.append({"name": folder, "description": desc, "files": files, "count": len(files)})
        return {"root": str(BRAIN_DIR), "folders": folders}

    def get_relevant_context(
        self,
        query: str,
        limit: int = 3,
        *,
        allowed_workspaces: Any = None,
    ) -> str:
        """질문과 관련된 정원 노트를 두뇌에서 검색해 컨텍스트로 반환.

        v4: 채팅마다 vault 전체를 rglob 하던 O(n) 스캔을 브레인 검색으로
        대체. 그래프가 없으면(비활성) 기존 파일 스캔으로 정직하게 폴백.
        """
        if self._kg is not None:
            try:
                scope_kwargs = (
                    {"allowed_workspaces": allowed_workspaces}
                    if allowed_workspaces is not None
                    else {}
                )
                matches = self._kg.search(
                    query,
                    max(limit * 4, 8),
                    **scope_kwargs,
                ).get("matches", [])
                results = []
                for match in matches:
                    meta = match.get("metadata") or {}
                    if not (meta.get("garden_folder") or meta.get("pipeline") == "p-reinforce"):
                        continue
                    title = match.get("title") or "note"
                    body = match.get("summary") or ""
                    results.append(f"--- Document: {title} ---\n{body[:800]}")
                    if len(results) >= limit:
                        break
                return "\n\n".join(results)
            except Exception as exc:
                logging.debug("garden brain context failed: %s", exc)
                if allowed_workspaces is not None:
                    return ""
        elif allowed_workspaces is not None:
            return ""
        return self._scan_vault_context(query, limit)

    def _scan_vault_context(self, query: str, limit: int = 3) -> str:
        results = []
        for file_path in BRAIN_DIR.rglob("*.md"):
            if file_path.name == "INDEX.md" or "40_Log" in str(file_path):
                continue
            try:
                content = file_path.read_text(encoding="utf-8")
                keywords = [k for k in re.split(r"\s+", query) if len(k) > 1]
                if any(k.lower() in content.lower() for k in keywords):
                    results.append(f"--- Document: {file_path.name} ---\n{content[:800]}")
                    if len(results) >= limit:
                        break
            except Exception:
                continue
        return "\n\n".join(results)
