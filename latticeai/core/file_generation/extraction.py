"""Recover the intended file payload from an arbitrary model reply.

Step 2 of the pipeline described in the package docstring: strip reasoning
blocks and conversational framing, pick the best fenced block, and slice known
document boundaries. Pure string work — nothing here decides whether the result
is *good*, only what the model most plausibly meant to hand over.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from latticeai.core.quiet import quiet

_THINK_BLOCK_RE = re.compile(
    r"<(think|thinking|reasoning|reflection)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
# Unclosed think block (model hit the token limit mid-reasoning).
_THINK_OPEN_RE = re.compile(r"<(think|thinking|reasoning)>.*\Z", re.DOTALL | re.IGNORECASE)

_FENCE_RE = re.compile(r"```([\w.+-]*)[ \t]*\n(.*?)```", re.DOTALL)

# Conversational lines that small models prepend/append around the payload.
_CHAT_LINE_RE = re.compile(
    r"^\s*("
    r"(sure|of course|certainly|okay|ok|alright|great|absolutely)\b[^\n]*"
    r"|here('s| is| are)\b[^\n]*"
    r"|i('ve| have) (created|written|generated|made)\b[^\n]*"
    r"|(below|following) is\b[^\n]*"
    r"|let me know\b[^\n]*"
    r"|hope (this|that) helps[^\n]*"
    r"|feel free\b[^\n]*"
    r"|물론(입니다|이죠|이에요)?[!., ]*[^\n]*"
    r"|네[,!. ][^\n]*"
    r"|알겠습니다[^\n]*"
    r"|다음은[^\n]*(입니다|합니다)[:.]?[^\n]*"
    r"|아래는?[^\n]*(입니다|내용)[^\n]*"
    r"|(요청하신|원하시는)[^\n]*(입니다|만들었습니다|작성했습니다)[^\n]*"
    r"|(파일|내용|코드)[을를]?\s*(생성|작성|만들)[^\n]*"
    r"|도움이 (필요하|되)[^\n]*"
    r"|추가로[^\n]*(말씀|요청)[^\n]*"
    r")\s*$",
    re.IGNORECASE,
)


# Language tags that identify a fenced block as the payload for an extension.
_EXT_FENCE_LANGS: Dict[str, Tuple[str, ...]] = {
    ".html": ("html", "htm", "xhtml"),
    ".htm": ("html", "htm", "xhtml"),
    ".css": ("css",),
    ".js": ("js", "javascript"),
    ".jsx": ("jsx", "javascript"),
    ".ts": ("ts", "typescript"),
    ".tsx": ("tsx", "typescript"),
    ".py": ("py", "python"),
    ".json": ("json",),
    ".yaml": ("yaml", "yml"),
    ".yml": ("yaml", "yml"),
    ".toml": ("toml",),
    ".md": ("md", "markdown"),
    ".markdown": ("md", "markdown"),
    ".sql": ("sql",),
    ".sh": ("sh", "bash", "shell", "zsh"),
    ".xml": ("xml", "svg"),
    ".csv": ("csv",),
    ".txt": ("txt", "text", "plaintext"),
    ".vue": ("vue", "html"),
    ".svelte": ("svelte", "html"),
}


def _ext(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot >= 0 else ""


def _strip_chat_lines(text: str) -> str:
    """Drop leading/trailing conversational lines around the payload."""
    lines = text.split("\n")
    start, end = 0, len(lines)
    while start < end and (not lines[start].strip() or _CHAT_LINE_RE.match(lines[start])):
        start += 1
    while end > start and (not lines[end - 1].strip() or _CHAT_LINE_RE.match(lines[end - 1])):
        end -= 1
    stripped = "\n".join(lines[start:end]).strip()
    return stripped if stripped else text.strip()


def extract_file_content(raw: str, target_path: str) -> str:
    """Recover the intended file payload from an arbitrary model reply."""
    text = (raw or "").strip()
    if not text:
        return ""
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_OPEN_RE.sub("", text).strip()

    ext = _ext(target_path)
    fences = _FENCE_RE.findall(text + ("\n```" if text.count("```") % 2 else ""))
    if fences:
        wanted = _EXT_FENCE_LANGS.get(ext, ())
        matching = [body for lang, body in fences if lang.lower() in wanted]
        candidates = matching if matching else [body for _, body in fences]
        # The payload is the largest block; short blocks are usually usage
        # snippets ("run it with: python app.py").
        content = max(candidates, key=len).strip()
    else:
        content = _strip_chat_lines(text)

    if ext in (".html", ".htm"):
        content = _slice_html_document(content)
    elif ext == ".json":
        sliced = _slice_json_document(content)
        if sliced is not None:
            content = sliced
    return content.strip()


def _slice_html_document(content: str) -> str:
    """Cut a complete HTML document out of surrounding prose when present."""
    lower = content.lower()
    start = lower.find("<!doctype")
    if start < 0:
        start = lower.find("<html")
    if start > 0:
        content = content[start:]
        lower = lower[start:]
    end = lower.rfind("</html>")
    if end >= 0:
        content = content[: end + len("</html>")]
    return content


def _slice_json_document(content: str) -> Optional[str]:
    """Return the largest parseable JSON value inside ``content``, if any."""
    candidates: List[str] = [content]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = content.find(opener)
        end = content.rfind(closer)
        if start >= 0 and end > start:
            candidates.append(content[start : end + 1])
    best: Optional[str] = None
    for candidate in candidates:
        try:
            json.loads(candidate)
        except (ValueError, TypeError):
            quiet()
            continue
        if best is None or len(candidate) > len(best):
            best = candidate
    return best
