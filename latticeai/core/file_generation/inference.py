"""What the user asked for, when they never said a filename.

Two deliberately narrow, fully deterministic inferences (weak local models
never see either decision): a single filename for "html 파일 만들어줘", and a
multi-file project manifest for "todo 앱 html+css+js로 만들어줘". Both require
a creation verb and an explicit type keyword, so anything less specific keeps
flowing to the paths that handled it before.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_CREATE_VERB_RE = re.compile(
    r"(만들|생성|작성|써\s*줘|저장|create|make|write|generate|build|save)",
    re.IGNORECASE,
)


# Explicit type keyword → default filename. Ordered: first match wins.
_TYPE_KEYWORDS: Tuple[Tuple[str, str], ...] = (
    (r"\bhtml\b|웹\s*페이지|웹페이지|홈페이지|landing\s*page|web\s*page", "generated_page.html"),
    (r"\bcss\b|스타일\s*시트", "styles.css"),
    (r"\bjavascript\b|\bjs\b\s*(파일|file)|자바스크립트", "script.js"),
    (r"\bpython\b|파이썬", "script.py"),
    (r"\bjson\b", "data.json"),
    (r"\bcsv\b", "data.csv"),
    (r"\byaml\b|\byml\b", "config.yaml"),
    (r"\bxml\b", "data.xml"),
    (r"\bsql\b", "query.sql"),
    (r"마크다운|\bmarkdown\b|\bmd\b\s*(파일|file)", "notes.md"),
    (r"텍스트\s*파일|\btext\s*file\b|\btxt\b", "notes.txt"),
)


def infer_file_target(message: str) -> Optional[str]:
    """Infer a filename for creation requests that name a type but no path.

    "html 파일 만들어줘" previously fell through to the agent JSON loop, which
    small models fail at. Inference keeps such requests on the deterministic
    direct-write path. Deliberately narrow: requires a creation verb and an
    explicit file-type keyword — report/document prose requests keep flowing
    to the document generator.
    """
    text = (message or "").strip()
    if not text or not _CREATE_VERB_RE.search(text):
        return None
    lower = text.lower()
    for pattern, filename in _TYPE_KEYWORDS:
        if re.search(pattern, lower):
            return filename
    return None


# ``\b`` fails against Korean particles ("js로") because Hangul is ``\w`` —
# use ASCII lookarounds so type keywords match with or without a particle.
_HTML_HINT_RE = re.compile(
    r"(?<![a-z0-9])html(?![a-z0-9])"
    r"|웹\s*페이지|웹페이지|홈페이지|웹\s*사이트|웹사이트|website|web\s*page|landing\s*page",
)
_CSS_HINT_RE = re.compile(r"(?<![a-z0-9])css(?![a-z0-9])|스타일\s*시트|stylesheet")
_JS_HINT_RE = re.compile(
    r"(?<![a-z0-9])js(?![a-z0-9])|javascript|자바스크립트|자바\s*스크립트"
)
# An explicit filename means the user is managing paths — keep the
# deterministic single-file flow untouched.
_EXPLICIT_FILENAME_RE = re.compile(
    r"[\w-]+\.(?:html?|css|js|jsx|ts|tsx|py|json|md|txt|csv|vue|svelte)\b",
    re.IGNORECASE,
)
_PROJECT_NAME_RE = re.compile(r"([A-Za-z][A-Za-z0-9_-]{1,30})\s*(?:앱|app\b)", re.IGNORECASE)
# React/Vite intent: the react keyword is specific enough on its own.
_REACT_HINT_RE = re.compile(r"(?<![a-z0-9])react(?![a-z0-9])|리액트")
_VITE_HINT_RE = re.compile(r"(?<![a-z0-9])vite(?![a-z0-9])")
# Python package intent: language + package word, both required.
_PYTHON_HINT_RE = re.compile(r"(?<![a-z0-9])python(?![a-z0-9])|파이썬")
_PACKAGE_HINT_RE = re.compile(r"패키지|(?<![a-z0-9])package(?![a-z0-9])")
_PKG_NAME_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9_-]{1,30})\s*(?:패키지|package\b)", re.IGNORECASE
)


def _react_manifest(text: str) -> Dict[str, Any]:
    """Vite + React starter manifest (review Wave 4: manifest 확장)."""
    name_match = _PROJECT_NAME_RE.search(text)
    name = f"{name_match.group(1).lower()}-app" if name_match else "react-app"
    return {
        "name": name,
        "kind": "react",
        "files": [
            {
                "path": "package.json",
                "brief": (
                    f'Vite React app manifest: strictly valid JSON with "name": "{name}", '
                    '"private": true, "type": "module", "scripts" {"dev": "vite", '
                    '"build": "vite build", "preview": "vite preview"}, "dependencies" '
                    'with react and react-dom (^18), and "devDependencies" with vite '
                    "and @vitejs/plugin-react."
                ),
            },
            {
                "path": "index.html",
                "brief": (
                    "The Vite entry HTML: <div id=\"root\"></div> in <body> and "
                    "<script type=\"module\" src=\"/src/main.jsx\"></script> just "
                    "before </body>. No inline styles or scripts."
                ),
            },
            {
                "path": "src/main.jsx",
                "brief": (
                    "React entry: createRoot from react-dom/client rendering <App /> "
                    "into #root; imports ./App.jsx and ./App.css."
                ),
            },
            {
                "path": "src/App.jsx",
                "brief": (
                    "The main App component implementing the user's request as one "
                    "self-contained React component (hooks allowed, no extra deps)."
                ),
            },
            {
                "path": "src/App.css",
                "brief": "All visual styles for the App component.",
            },
        ],
    }


def _python_package_manifest(text: str) -> Dict[str, Any]:
    """Multi-file Python package manifest (review Wave 4: manifest 확장)."""
    name_match = _PKG_NAME_RE.search(text)
    raw_name = name_match.group(1).lower() if name_match else "my_package"
    module = re.sub(r"[^a-z0-9_]", "_", raw_name)
    if not re.match(r"[a-z_]", module):
        module = f"pkg_{module}"
    return {
        "name": module,
        "kind": "python",
        "files": [
            {
                "path": f"{module}/__init__.py",
                "brief": (
                    f"Package init for {module}: import and re-export the public "
                    "API from .core with an explicit __all__."
                ),
            },
            {
                "path": f"{module}/core.py",
                "brief": (
                    "Implement the user's request as clean, documented functions/"
                    "classes with type hints. Standard library only."
                ),
            },
            {
                "path": f"{module}/cli.py",
                "brief": (
                    "argparse CLI wrapping the core API: a main() function and an "
                    'if __name__ == "__main__": main() guard.'
                ),
            },
            {
                "path": "README.md",
                "brief": (
                    f"Usage documentation for the {module} package: install, import "
                    "example, and CLI example."
                ),
            },
        ],
    }


def infer_project_manifest(message: str) -> Optional[Dict[str, Any]]:
    """Infer a multi-file project manifest from a creation request.

    "todo 앱 html+css+js로 만들어줘" should yield real linked files, not one
    inlined page. Deliberately narrow and deterministic (weak local models
    never see this decision): requires a creation verb, a recognized project
    intent (web page + css/js, React/Vite app, or Python package), and no
    explicit filename. Single-type requests return ``None`` so the existing
    single-file flow is completely unchanged.
    """
    text = (message or "").strip()
    if not text or not _CREATE_VERB_RE.search(text):
        return None
    if _EXPLICIT_FILENAME_RE.search(text):
        return None
    lower = text.lower()

    # Most-specific first: React (its own structure), then Python package,
    # then the classic html+css/js web bundle.
    if _REACT_HINT_RE.search(lower) or _VITE_HINT_RE.search(lower):
        return _react_manifest(text)
    if _PYTHON_HINT_RE.search(lower) and _PACKAGE_HINT_RE.search(lower):
        return _python_package_manifest(text)

    wants_html = bool(_HTML_HINT_RE.search(lower))
    wants_css = bool(_CSS_HINT_RE.search(lower))
    wants_js = bool(_JS_HINT_RE.search(lower))
    if not wants_html or not (wants_css or wants_js):
        return None

    name_match = _PROJECT_NAME_RE.search(text)
    name = f"{name_match.group(1).lower()}-app" if name_match else "web-project"

    files: List[Dict[str, str]] = []
    html_refs: List[str] = []
    if wants_css:
        html_refs.append('<link rel="stylesheet" href="style.css"> in <head>')
    if wants_js:
        html_refs.append('<script src="app.js"></script> just before </body>')
    files.append({
        "path": "index.html",
        "brief": (
            "The main HTML page of the project. Reference the sibling files: "
            + " and ".join(html_refs)
            + ". Do not inline styles or behavior scripts."
        ),
    })
    if wants_css:
        files.append({
            "path": "style.css",
            "brief": "All visual styles for index.html (layout, colors, typography).",
        })
    if wants_js:
        files.append({
            "path": "app.js",
            "brief": (
                "All page behavior for index.html as plain browser JavaScript "
                "(no build step, no imports of missing files)."
            ),
        })
    return {"name": name, "kind": "web", "files": files}
