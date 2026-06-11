"""Filesystem tools confined to the agent workspace.

read/write/edit/grep/search, todo list, HTML inspection and web scaffolding.
Path resolution reads ``tools.AGENT_ROOT`` so tests can redirect the sandbox.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional

import tools
from tools import (
    ToolError,
    ensure_agent_root,
    _resolve_path,
    _relative,
    MAX_FILE_BYTES,
    TEXT_EXTENSIONS,
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


def write_file(path: str, content: str) -> Dict[str, Any]:
    target = _resolve_path(path)
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ToolError("Content is too large to write.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"path": _relative(target), "bytes": target.stat().st_size}


def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> Dict[str, Any]:
    """Precise diff-style edit: replace `old_string` with `new_string` in `path`.

    Fails when `old_string` is missing or appears more than once (unless
    replace_all=True). This forces the caller to read the file first and pass
    enough surrounding context to uniquely identify the edit site — the same
    discipline Claude Code uses for safe edits.
    """
    if old_string == new_string:
        raise ToolError("old_string and new_string are identical; nothing to change.")
    target = _resolve_path(path)
    if not target.exists() or not target.is_file():
        raise ToolError("File does not exist.")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise ToolError("File is too large to edit.")

    original = target.read_text(encoding="utf-8")
    occurrences = original.count(old_string)
    if occurrences == 0:
        raise ToolError("old_string not found in file. Read the file first and copy the exact bytes (including whitespace).")
    if occurrences > 1 and not replace_all:
        raise ToolError(f"old_string is ambiguous: appears {occurrences} times. Add more context to make it unique, or pass replace_all=true.")

    updated = original.replace(old_string, new_string) if replace_all else original.replace(old_string, new_string, 1)
    if len(updated.encode("utf-8")) > MAX_FILE_BYTES:
        raise ToolError("Resulting file would exceed the workspace size limit.")
    target.write_text(updated, encoding="utf-8")

    edited_line = original[: original.find(old_string)].count("\n") + 1
    return {
        "path": _relative(target),
        "replacements": occurrences if replace_all else 1,
        "bytes": target.stat().st_size,
        "first_edit_line": edited_line,
    }


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


def todo_write(todos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Replace the agent's TODO list. Each todo: {id, content, status}.

    Status must be one of: pending, in_progress, completed.
    At most one todo should be in_progress at any time — the agent enforces
    this convention; the tool only warns if violated.
    """
    if not isinstance(todos, list):
        raise ToolError("todos must be a list.")
    if len(todos) > 50:
        raise ToolError("Too many todos (max 50). Split into smaller batches.")

    cleaned: List[Dict[str, Any]] = []
    in_progress_count = 0
    for idx, raw in enumerate(todos, start=1):
        if not isinstance(raw, dict):
            raise ToolError(f"Todo #{idx} is not an object.")
        content = str(raw.get("content") or "").strip()
        if not content:
            raise ToolError(f"Todo #{idx} is missing 'content'.")
        status = str(raw.get("status") or "pending").strip().lower()
        if status not in _TODO_ALLOWED_STATUS:
            raise ToolError(f"Todo #{idx} has invalid status '{status}'. Use one of {sorted(_TODO_ALLOWED_STATUS)}.")
        if status == "in_progress":
            in_progress_count += 1
        cleaned.append({
            "id": str(raw.get("id") or idx),
            "content": content[:240],
            "status": status,
        })

    target = _todo_file()
    target.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "todos": cleaned,
        "path": _TODO_REL_PATH,
        "warning": "More than one todo is in_progress; keep only one active at a time." if in_progress_count > 1 else None,
    }


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
        elif tag == "link" and attr.get("rel") and "stylesheet" in " ".join(attr.get("rel", [])):
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


def create_web_project(path: str, framework: str = "react", template: str = "vite") -> Dict[str, Any]:
    framework = str(framework or "").strip().lower()
    template = str(template or "").strip().lower()
    if framework != "react" or template != "vite":
        raise ToolError("Only React + Vite template is currently supported.")
    if not path:
        raise ToolError("Project path is required.")

    root = _resolve_path(path)
    root.mkdir(parents=True, exist_ok=True)

    files = {
        "package.json": json.dumps(
            {
                "name": Path(path).name.replace(" ", "-").lower() or "vite-react-app",
                "private": True,
                "version": "0.0.0",
                "type": "module",
                "scripts": {
                    "dev": "vite",
                    "build": "vite build",
                    "preview": "vite preview",
                },
                "dependencies": {
                    "react": "^18.3.1",
                    "react-dom": "^18.3.1",
                },
                "devDependencies": {
                    "@vitejs/plugin-react": "^4.3.1",
                    "vite": "^5.4.0",
                },
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        "index.html": """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Vite React App</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
""",
        "vite.config.js": """import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
})
""",
        "README.md": """# Vite React App

## Run

```bash
npm install
npm run dev
```

## Build

```bash
npm run build
npm run preview
```

## Lattice AI Notes

- Inspect `package.json` and existing config files before adding new libraries.
- If you add Tailwind CSS, framer-motion, TypeScript, or other tooling, add the required config files too.
- Do not report the app as complete until `npm run build` succeeds.
""",
        "src/main.jsx": """import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
""",
        "src/App.jsx": """import { useState } from 'react'

export default function App() {
  const [count, setCount] = useState(0)
  return (
    <main style={{ maxWidth: 760, margin: '48px auto', padding: '0 20px', fontFamily: 'system-ui, sans-serif' }}>
      <h1>Vite + React</h1>
      <p>Starter generated by Lattice AI agent.</p>
      <p style={{ color: '#555', lineHeight: 1.6 }}>
        Inspect the current setup before adding new UI libraries, then verify
        changes with <code>npm run build</code>.
      </p>
      <button onClick={() => setCount((c) => c + 1)}>count is {count}</button>
    </main>
  )
}
""",
        "src/index.css": """* { box-sizing: border-box; }
body { margin: 0; background: #f6f7fb; color: #111; }
button { padding: 10px 14px; border-radius: 10px; border: 1px solid #d6d6d6; background: #fff; cursor: pointer; }
""",
    }

    created: List[str] = []
    total_bytes = 0
    for rel_path, content in files.items():
        target = (root / rel_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        created.append(_relative(target))
        total_bytes += target.stat().st_size

    return {
        "path": _relative(root),
        "framework": framework,
        "template": template,
        "created_files": created,
        "file_count": len(created),
        "bytes": total_bytes,
    }
