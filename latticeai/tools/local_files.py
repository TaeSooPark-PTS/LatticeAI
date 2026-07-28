"""Local filesystem tools (require user approval via the UI) + desktop bridge status."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from latticeai.core.tool_registry import LOCAL_WRITE_BLOCKED_PREFIXES
from latticeai.tools import LOCAL_MAX_FILE_BYTES, ToolError


def local_list(path: str) -> Dict[str, Any]:
    """List any directory on the local filesystem (requires user approval via UI)."""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise ToolError(f"경로가 존재하지 않습니다: {path}")
    if not target.is_dir():
        raise ToolError(f"폴더가 아닙니다: {path}")
    items = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            stat = child.stat()
            items.append({
                "name": child.name,
                "path": str(child),
                "type": "directory" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else None,
            })
    except PermissionError as exc:
        raise ToolError(f"접근 권한 없음: {exc}") from exc
    return {"path": str(target), "items": items}


def local_read(path: str) -> Dict[str, Any]:
    """Read any file on the local filesystem (requires user approval via UI)."""
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise ToolError(f"파일이 존재하지 않습니다: {path}")
    if not target.is_file():
        raise ToolError(f"파일이 아닙니다: {path}")
    size = target.stat().st_size
    if size > LOCAL_MAX_FILE_BYTES:
        raise ToolError(f"파일이 너무 큽니다 ({size:,} bytes). 최대 {LOCAL_MAX_FILE_BYTES:,} bytes.")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        raise ToolError(f"파일 읽기 실패: {exc}") from exc
    return {"path": str(target), "size": size, "content": content}


def local_write(path: str, content: str) -> Dict[str, Any]:
    """Write content to any path on the local filesystem (requires user approval via UI)."""
    target = Path(path).expanduser().resolve()
    normalized = str(target).replace("\\", "/")
    for prefix in LOCAL_WRITE_BLOCKED_PREFIXES:
        blocked = normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        if blocked:
            raise ToolError("차단된 시스템 경로에는 쓸 수 없습니다.")
    if len(content.encode("utf-8")) > LOCAL_MAX_FILE_BYTES:
        raise ToolError("내용이 너무 큽니다.")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except PermissionError as exc:
        raise ToolError(f"쓰기 권한 없음: {exc}") from exc
    return {"path": str(target), "bytes": target.stat().st_size}



def desktop_bridge_status() -> Dict[str, Any]:
    return {
        "status": "requires_desktop_bridge",
        "available_in_codex": True,
        "note": "Chrome and Mac UI control require the Codex desktop Computer Use/Chrome bridge, not a headless FastAPI worker.",
    }
