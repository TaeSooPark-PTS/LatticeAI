"""Per-extension structural checks — step 3 of the pipeline.

Answers one question about an already-extracted payload: *is this a file of the
requested type?* HTML document shape, JSON parses, CSS has rule blocks, Python
parses, braced code is balanced, components close their blocks — and, for prose
types with no grammar to check, that the reply is not merely talk about the
file.
"""

from __future__ import annotations

import ast
import json
import re
from typing import List, Tuple

from .extraction import _ext

_REFUSAL_RE = re.compile(
    r"(i can('|no)?t|i'?m (sorry|unable)|as an ai|cannot assist"
    r"|죄송(하지만|합니다)|할 수 없|불가능합니다|도와드릴 수 없)",
    re.IGNORECASE,
)


def looks_like_refusal(content: str) -> bool:
    head = content[:300]
    return bool(_REFUSAL_RE.search(head)) and len(content) < 600


# Braced code types validated structurally (balanced delimiters, no fences).
_BRACED_CODE_EXTENSIONS = frozenset({".js", ".jsx", ".ts", ".tsx"})
# Single-file components validated by their block tags being closed.
_COMPONENT_EXTENSIONS = frozenset({".vue", ".svelte"})


def _strip_code_literals(text: str) -> str:
    """Remove string literals and comments so delimiter counting stays honest.

    A cheap single-pass scanner (not a parser): quotes ('', "", ``), line
    comments (//) and block comments (/* */) commonly contain lone braces
    that would otherwise false-flag valid JS/TS as unbalanced.
    """
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "\\":
            i += 2  # escaped char (inside or outside a literal — always skip)
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _check_balanced_delimiters(content: str) -> Tuple[bool, str]:
    """Lenient count-based balance check for braced code (js/ts family).

    Only *counts* are compared, never ordering, so valid-but-unusual code is
    not rejected; a truncated file with a dangling ``{`` still fails.
    """
    stripped = _strip_code_literals(content)
    for opener, closer, label in (("{", "}", "braces"), ("(", ")", "parentheses"), ("[", "]", "brackets")):
        if stripped.count(opener) != stripped.count(closer):
            return False, f"unbalanced {label} ({opener}{closer}) — the file looks truncated"
    return True, "ok"


def _check_component_blocks(content: str) -> Tuple[bool, str]:
    """Vue/Svelte SFC sanity: every opened block tag must be closed."""
    lower = content.lower()
    for tag in ("template", "script", "style"):
        opened = len(re.findall(rf"<{tag}(?:\s[^>]*)?>", lower))
        closed = lower.count(f"</{tag}>")
        if opened != closed:
            return False, f"<{tag}> block is not closed — the component looks truncated"
    return True, "ok"


def validate_file_content(content: str, target_path: str) -> Tuple[bool, str]:
    """Structural sanity check per file type. Returns (ok, reason)."""
    if not content.strip():
        return False, "empty output"
    if looks_like_refusal(content):
        return False, "the reply was a refusal/chat message, not file content"

    ext = _ext(target_path)
    if ext in (".html", ".htm"):
        lower = content.lower()
        if "<html" not in lower and "<!doctype" not in lower:
            return False, "not a complete HTML document (missing <!DOCTYPE html>/<html>)"
        if "</html>" not in lower:
            return False, "HTML document is truncated (missing </html>)"
        # A document that merely *contains* html somewhere is not a valid
        # file payload: fenced/chat-wrapped replies must fail here so the
        # extraction pass gets a chance to slice out the real document.
        stripped = content.lstrip()
        if not (stripped.lower().startswith("<!doctype") or stripped.lower().startswith("<html")):
            return False, "HTML document is wrapped in prose or fences"
        if "```" in content:
            return False, "output still contains Markdown fences"
        return True, "ok"
    if ext == ".json":
        try:
            json.loads(content)
        except (ValueError, TypeError) as exc:
            return False, f"invalid JSON: {exc}"
        return True, "ok"
    if ext == ".css":
        if "```" in content:
            return False, "output still contains Markdown fences"
        if "{" not in content or "}" not in content:
            return False, "no CSS rule blocks found"
        return True, "ok"
    if ext == ".py":
        if "```" in content:
            return False, "output still contains Markdown fences"
        try:
            ast.parse(content)
        except SyntaxError as exc:
            return False, f"invalid Python syntax: {exc.msg} (line {exc.lineno})"
        return True, "ok"
    if ext in _BRACED_CODE_EXTENSIONS:
        if "```" in content:
            return False, "output still contains Markdown fences"
        return _check_balanced_delimiters(content)
    if ext in _COMPONENT_EXTENSIONS:
        if "```" in content:
            return False, "output still contains Markdown fences"
        return _check_component_blocks(content)
    if ext in (".sh", ".sql"):
        if "```" in content:
            return False, "output still contains Markdown fences"
        return True, "ok"
    # Prose types (.md, .txt, .csv, …) have no grammar to check, which used to
    # mean *nothing* was checked: a 1–4B model that answered "Sure! Here is the
    # document you asked for:" and stopped had its sentence saved as the file,
    # because the fence stripper only removes conversational lines it can
    # recognise and the length guard on `looks_like_refusal` lets a wordy
    # refusal through. The two checks below are the only ones that generalise
    # without inventing a grammar: it must not still be wearing fences, and it
    # must not be *only* an answer about the file.
    if "```" in content:
        return False, "output still contains Markdown fences"
    if _looks_like_commentary(content):
        return False, "the reply talks about the file instead of being the file"
    return True, "ok"


# Openers that mean "I am about to give you the thing" — if the whole reply is
# one of these, the thing never arrived.
_COMMENTARY_RE = re.compile(
    r"^\s*("
    r"(sure|of course|certainly|okay|ok|alright|here|below|the following)\b"
    r"|i('ve| have| will|'ll)\b"
    r"|(물론|네[,!. ]|알겠|다음은|아래(는|의)?|요청하신|원하시는)"
    r")",
    re.IGNORECASE,
)


def _looks_like_commentary(content: str) -> bool:
    """True when the reply reads as an answer *about* a file, not the file.

    Deliberately conservative — a long document that merely opens with "The
    following" is a document. Only a short reply that both opens
    conversationally and never grows into content is rejected, so a real file
    is never thrown away to catch a chat line.
    """
    stripped = content.strip()
    if len(stripped) > 400:
        return False
    if not _COMMENTARY_RE.match(stripped):
        return False
    # Structure means content arrived after the preamble: a heading, a list, a
    # table row, a delimiter, or simply several lines of body text.
    body = stripped.split("\n", 1)[1].strip() if "\n" in stripped else ""
    if re.search(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|\||>)", body, re.MULTILINE):
        return False
    return len(body) < 120
