"""Knowledge-base / Obsidian vault tools over the local brain directory."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from p_reinforce import BRAIN_DIR, STRUCTURE
from tools import MAX_FILE_BYTES, ToolError


def _safe_brain_folder(folder: str) -> str:
    if folder not in STRUCTURE:
        raise ToolError(f"Unknown knowledge folder: {folder}")
    return folder


def knowledge_save(content: str, folder: str = "00_Raw", title: Optional[str] = None) -> Dict[str, Any]:
    folder = _safe_brain_folder(folder)
    if not content:
        raise ToolError("Knowledge content is required.")
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ToolError("Knowledge content is too large.")

    target_dir = BRAIN_DIR / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_title = title or content.strip().splitlines()[0][:60] or "note"
    safe_title = "".join(ch if ch.isalnum() or ch in (" ", "-", "_") else "" for ch in safe_title).strip()
    safe_title = "_".join(safe_title.split()) or "note"
    filename = f"{safe_title}.md"
    target = target_dir / filename
    counter = 2
    while target.exists():
        target = target_dir / f"{safe_title}_{counter}.md"
        counter += 1
    target.write_text(content, encoding="utf-8")
    return {"folder": folder, "filename": target.name, "path": str(target)}


def knowledge_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    if not query:
        raise ToolError("Query is required.")
    max_results = max(1, min(int(max_results), 20))
    query_lower = query.lower()
    results: List[Dict[str, Any]] = []

    for file_path in BRAIN_DIR.rglob("*.md"):
        if len(results) >= max_results:
            break
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if query_lower in content.lower() or query_lower in file_path.name.lower():
            results.append(
                {
                    "path": str(file_path),
                    "relative_path": str(file_path.relative_to(BRAIN_DIR)),
                    "preview": content[:500],
                }
            )

    return {"query": query, "results": results}


def knowledge_tree() -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for folder in STRUCTURE:
        root = BRAIN_DIR / folder
        root.mkdir(parents=True, exist_ok=True)
        for file_path in sorted(root.rglob("*.md")):
            entries.append(
                {
                    "folder": folder,
                    "relative_path": str(file_path.relative_to(BRAIN_DIR)),
                    "size": file_path.stat().st_size,
                }
            )
    return {"root": str(BRAIN_DIR), "entries": entries}


def obsidian_save(content: str, folder: str = "00_Raw", title: Optional[str] = None) -> Dict[str, Any]:
    result = knowledge_save(content, folder, title)
    result["vault_root"] = str(BRAIN_DIR)
    result["obsidian_uri_hint"] = f"obsidian://open?path={result['path']}"
    return result


def obsidian_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    result = knowledge_search(query, max_results)
    result["vault_root"] = str(BRAIN_DIR)
    return result


def obsidian_tree() -> Dict[str, Any]:
    return knowledge_tree()
