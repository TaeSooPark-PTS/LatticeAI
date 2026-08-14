"""Filesystem tools confined to the agent workspace.

read/write/edit/grep/search, todo list, HTML inspection and web scaffolding.
Path resolution reads ``latticeai.tools.AGENT_ROOT`` so tests can redirect the sandbox.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

import latticeai.tools as tools
from latticeai.core.quiet import quiet
from latticeai.tools import (
    MAX_FILE_BYTES,
    TEXT_EXTENSIONS,
    ToolError,
    _relative,
    _resolve_path,
    ensure_agent_root,
)


def list_dir(path: str = ".") -> Dict[str, Any]:
    target = _resolve_path(path)
    if not target.exists():
        raise ToolError("Directory does not exist.")
    if not target.is_dir():
        raise ToolError("Path is not a directory.")

    items: List[Dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        items.append(
            {
                "name": child.name,
                "path": _relative(child),
                "type": "directory" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            }
        )
    return {"root": str(tools.AGENT_ROOT), "path": _relative(target) if target != tools.AGENT_ROOT else ".", "items": items}


def workspace_tree(path: str = ".", max_depth: int = 3) -> Dict[str, Any]:
    target = _resolve_path(path)
    if not target.exists() or not target.is_dir():
        raise ToolError("Path is not a directory.")

    max_depth = max(1, min(int(max_depth), 8))
    entries: List[Dict[str, Any]] = []

    def walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for child in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            rel = _relative(child)
            entries.append(
                {
                    "path": rel,
                    "type": "directory" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                    "depth": depth,
                }
            )
            if child.is_dir():
                walk(child, depth + 1)

    walk(target, 1)
    return {"root": str(tools.AGENT_ROOT), "path": _relative(target) if target != tools.AGENT_ROOT else ".", "entries": entries}


def read_file(path: str, offset: int = 0, limit: int = 0, line_numbers: bool = True) -> Dict[str, Any]:
    """Read a file from the agent workspace.

    Returns content as plain text. When line_numbers is True (default), also
    returns a numbered view (`numbered`) plus `total_lines` so the agent can
    cite file:line locations precisely.

    offset is 0-indexed (the first line is offset=0). limit=0 reads to the end.
    """
    target = _resolve_path(path)
    if not target.exists():
        raise ToolError("File does not exist.")
    if not target.is_file():
        raise ToolError("Path is not a file.")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ToolError(f"File is too large to read ({size} bytes).")
    text = target.read_text(encoding="utf-8")
    all_lines = text.splitlines()
    total_lines = len(all_lines)

    offset = max(0, int(offset or 0))
    limit = max(0, int(limit or 0))
    end = total_lines if limit == 0 else min(total_lines, offset + limit)
    sliced = all_lines[offset:end]
    sliced_text = "\n".join(sliced)
    if offset == 0 and limit == 0 and text.endswith("\n"):
        sliced_text += "\n"

    result: Dict[str, Any] = {
        "path": _relative(target),
        "content": sliced_text,
        "total_lines": total_lines,
        "start_line": offset + 1,
        "end_line": end,
    }
    if line_numbers:
        width = max(4, len(str(end or total_lines)))
        result["numbered"] = "\n".join(
            f"{(offset + i + 1):>{width}}\t{line}" for i, line in enumerate(sliced)
        )
    return result


_GREP_BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar",
                    ".gz", ".bz2", ".xz", ".7z", ".mp3", ".mp4", ".mov", ".wav",
                    ".woff", ".woff2", ".ttf", ".eot", ".ico", ".db", ".sqlite",
                    ".pyc", ".pyo", ".o", ".so", ".dylib", ".dll", ".exe", ".bin"}
_GREP_BINARY_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__",
                    ".pytest_cache", "dist", "build", ".next", ".cache"}


def grep(
    pattern: str,
    path: str = ".",
    glob: Optional[str] = None,
    max_results: int = 50,
    case_insensitive: bool = False,
    context_lines: int = 0,
) -> Dict[str, Any]:
    """Regex search across the agent workspace.

    Unlike `search_files` (single line, 9 extensions, substring only), this
    walks all text files, supports regex, returns line numbers, and can
    optionally include surrounding context lines. Skips obvious binary
    files/directories.
    """
    if not pattern:
        raise ToolError("Pattern is required.")
    try:
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)
    except re.error as exc:
        raise ToolError(f"Invalid regex: {exc}") from exc

    target = _resolve_path(path)
    if not target.exists() or not target.is_dir():
        raise ToolError("Path is not a directory.")

    max_results = max(1, min(int(max_results), 500))
    context_lines = max(0, min(int(context_lines), 8))
    matches: List[Dict[str, Any]] = []
    files_scanned = 0
    files_with_matches = 0

    iterator = target.rglob(glob) if glob else target.rglob("*")
    for file_path in iterator:
        if len(matches) >= max_results:
            break
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() in _GREP_BINARY_EXTS:
            continue
        if any(part in _GREP_BINARY_DIRS for part in file_path.parts):
            continue
        if file_path.stat().st_size > MAX_FILE_BYTES:
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            quiet()
            continue

        files_scanned += 1
        file_had_match = False
        for index, line in enumerate(lines, start=1):
            if len(matches) >= max_results:
                break
            if not regex.search(line):
                continue
            file_had_match = True
            entry: Dict[str, Any] = {
                "path": _relative(file_path),
                "line": index,
                "match": line[:400],
            }
            if context_lines:
                lo = max(0, index - 1 - context_lines)
                hi = min(len(lines), index + context_lines)
                entry["context"] = [
                    {"line": lo + i + 1, "text": lines[lo + i][:200]}
                    for i in range(hi - lo)
                ]
            matches.append(entry)
        if file_had_match:
            files_with_matches += 1

    return {
        "pattern": pattern,
        "matches": matches,
        "files_scanned": files_scanned,
        "files_with_matches": files_with_matches,
        "truncated": len(matches) >= max_results,
    }


_TODO_REL_PATH = ".lattice/todos.json"
_TODO_ALLOWED_STATUS = {"pending", "in_progress", "completed"}


def _todo_file() -> Path:
    ensure_agent_root()
    target = tools.AGENT_ROOT / _TODO_REL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def todo_read() -> Dict[str, Any]:
    """Read the agent's persistent TODO list (per-workspace)."""
    target = _todo_file()
    if not target.exists():
        return {"todos": [], "path": _TODO_REL_PATH}
    try:
        todos = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        todos = []
    if not isinstance(todos, list):
        todos = []
    return {"todos": todos, "path": _TODO_REL_PATH}


def search_files(query: str, path: str = ".", max_results: int = 20) -> Dict[str, Any]:
    if not query:
        raise ToolError("Query is required.")
    target = _resolve_path(path)
    if not target.exists() or not target.is_dir():
        raise ToolError("Path is not a directory.")

    max_results = max(1, min(int(max_results), 100))
    matches: List[Dict[str, Any]] = []
    query_lower = query.lower()

    for file_path in target.rglob("*"):
        if len(matches) >= max_results:
            break
        if not file_path.is_file() or file_path.stat().st_size > MAX_FILE_BYTES:
            continue
        if file_path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            quiet()
            continue
        for index, line in enumerate(lines, start=1):
            if query_lower in line.lower():
                matches.append({"path": _relative(file_path), "line": index, "preview": line[:240]})
                break

    return {"query": query, "matches": matches}


class _HTMLInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self.links: List[str] = []
        self.scripts: List[str] = []
        self.stylesheets: List[str] = []
        self.images: List[str] = []
        self.forms = 0
        self.headings: List[Dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        attr = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "a" and attr.get("href"):
            self.links.append(attr["href"])
        elif tag == "script" and attr.get("src"):
            self.scripts.append(attr["src"])
        # ``HTMLParser`` hands every attribute value over as a string, so rel is
        # split on whitespace (rel="stylesheet preload" is two tokens) rather
        # than joined, and matched case-insensitively.
        elif tag == "link" and "stylesheet" in str(attr.get("rel") or "").lower().split():
            if attr.get("href"):
                self.stylesheets.append(attr["href"])
        elif tag == "img" and attr.get("src"):
            self.images.append(attr["src"])
        elif tag == "form":
            self.forms += 1
        elif tag in {"h1", "h2", "h3"}:
            self.headings.append({"level": tag, "text": ""})

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self.title += text
        elif self.headings and not self.headings[-1]["text"]:
            self.headings[-1]["text"] = text[:120]


def inspect_html(path: str) -> Dict[str, Any]:
    target = _resolve_path(path)
    if not target.exists() or not target.is_file():
        raise ToolError("HTML file does not exist.")
    if target.suffix.lower() not in {".html", ".htm"}:
        raise ToolError("Path is not an HTML file.")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise ToolError("HTML file is too large to inspect.")

    parser = _HTMLInspector()
    parser.feed(target.read_text(encoding="utf-8"))
    return {
        "path": _relative(target),
        "title": parser.title,
        "links": parser.links[:50],
        "scripts": parser.scripts[:50],
        "stylesheets": parser.stylesheets[:50],
        "images": parser.images[:50],
        "forms": parser.forms,
        "headings": [h for h in parser.headings if h["text"]][:30],
    }


def preview_url(path: str = "index.html") -> Dict[str, Any]:
    target = _resolve_path(path)
    if not target.exists() or not target.is_file():
        raise ToolError("Preview file does not exist.")
    rel = _relative(target)
    return {
        "path": rel,
        "local_url": f"http://127.0.0.1:4825/agent-files/{rel}",
        "note": "Use the server host or /web Telegram link host instead of 127.0.0.1 from a phone.",
    }


# Generous cap for a generated-project archive — the workspace itself caps
# individual files at MAX_FILE_BYTES, this only bounds pathological trees.
MAX_ZIP_TOTAL_BYTES = 50_000_000


