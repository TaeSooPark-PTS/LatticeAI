"""Model-agnostic file content generation pipeline.

Small local models (gemma/qwen/llama 7B class) asked to "generate an HTML
file" commonly wrap the payload in chat noise: leading commentary ("Sure!
Here is your page:"), Markdown fences, ``<think>`` reasoning blocks,
trailing explanations, or an incomplete document. The previous direct-write
path saved that reply nearly verbatim, so weak models produced broken files.

This module makes the file-creation flow robust regardless of which LLM is
loaded, by treating the model as an untrusted content source:

1. Prompt   — extension-aware instructions anchored with the exact first
   line the reply must start with (small models follow examples, not rules).
2. Extract  — strip reasoning blocks and conversational framing, pick the
   best fenced block, slice known document boundaries.
3. Validate — per-extension structural checks (HTML document shape, JSON
   parses, CSS has rule blocks, refusal/chat detection).
4. Retry    — one corrective attempt that tells the model what was wrong.
5. Repair   — deterministic scaffolds guarantee the user still gets a valid
   file even when the model never produces usable output.

The pipeline is pure (no I/O, no FastAPI); the chat layer injects an async
``generate(context) -> str`` callable.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

# ── extraction ──────────────────────────────────────────────────────────

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

_REFUSAL_RE = re.compile(
    r"(i can('|no)?t|i'?m (sorry|unable)|as an ai|cannot assist"
    r"|죄송(하지만|합니다)|할 수 없|불가능합니다|도와드릴 수 없)",
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
            continue
        if best is None or len(candidate) > len(best):
            best = candidate
    return best


# ── validation ──────────────────────────────────────────────────────────

def looks_like_refusal(content: str) -> bool:
    head = content[:300]
    return bool(_REFUSAL_RE.search(head)) and len(content) < 600


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
        return True, "ok"
    if ext == ".json":
        try:
            json.loads(content)
        except (ValueError, TypeError) as exc:
            return False, f"invalid JSON: {exc}"
        return True, "ok"
    if ext == ".css":
        if "{" not in content or "}" not in content:
            return False, "no CSS rule blocks found"
        return True, "ok"
    if ext in (".py", ".js", ".jsx", ".ts", ".tsx", ".sh", ".sql"):
        if "```" in content:
            return False, "output still contains Markdown fences"
        return True, "ok"
    return True, "ok"


# ── prompting ───────────────────────────────────────────────────────────

_FIRST_LINE_HINTS: Dict[str, str] = {
    ".html": "<!DOCTYPE html>",
    ".htm": "<!DOCTYPE html>",
    ".py": "# (python code — imports or code on the first line)",
    ".sh": "#!/bin/sh",
    ".json": "{",
    ".xml": "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
}

_TYPE_RULES: Dict[str, str] = {
    ".html": (
        "Produce ONE complete standalone HTML5 document: <!DOCTYPE html>, <html>, "
        "<head> with <meta charset=\"utf-8\"> and a <title>, inline <style> for CSS, "
        "and a closed </html> tag. Do not reference external files."
    ),
    ".htm": (
        "Produce ONE complete standalone HTML5 document ending with </html>."
    ),
    ".json": "Produce strictly valid JSON (double quotes, no comments, no trailing commas).",
    ".css": "Produce valid CSS rules only.",
    ".md": "Produce well-structured Markdown with headings.",
    ".markdown": "Produce well-structured Markdown with headings.",
    ".csv": "Produce CSV with a header row; comma-separated, one record per line.",
    ".py": "Produce complete runnable Python source code.",
    ".js": "Produce complete valid JavaScript source code.",
}


def build_file_generation_context(
    target_path: str,
    user_request: str,
    feedback: Optional[str] = None,
) -> str:
    """Strict, extension-aware generation instructions.

    Small models ignore abstract rules but reliably imitate concrete anchors,
    so the prompt pins the exact first line of the expected output.
    """
    ext = _ext(target_path)
    parts = [
        "You are a file content generator. Your entire reply is saved verbatim "
        f"as the file `{target_path}` — it is NOT shown in a chat.",
        "Rules:",
        "- Output ONLY the raw file content.",
        "- No Markdown code fences (```), no explanations, no greetings, "
        "no text before or after the content.",
    ]
    type_rule = _TYPE_RULES.get(ext)
    if type_rule:
        parts.append(f"- {type_rule}")
    first_line = _FIRST_LINE_HINTS.get(ext)
    if first_line:
        parts.append(f"- The very first line of your reply must be: {first_line}")
    if feedback:
        parts.append(
            "Your previous attempt was rejected: "
            f"{feedback}. Fix that and output only the corrected file content."
        )
    parts.append(f"\nUser request: {user_request}")
    return "\n".join(parts)


# ── repair (deterministic fallback) ─────────────────────────────────────

def repair_file_content(content: str, target_path: str, user_request: str) -> str:
    """Turn whatever the model produced into a valid file of the target type.

    This is the last resort after retries: the user asked for a file, so the
    request must still end in a well-formed file, never an error.
    """
    ext = _ext(target_path)
    salvage = content.strip()
    if looks_like_refusal(salvage):
        salvage = ""

    if ext in (".html", ".htm"):
        return _repair_html(salvage, user_request)
    if ext == ".json":
        sliced = _slice_json_document(salvage)
        if sliced is not None:
            return sliced
        return json.dumps(
            {"request": user_request, "content": salvage},
            ensure_ascii=False,
            indent=2,
        )
    if salvage:
        return salvage
    # Nothing usable at all — leave an honest placeholder in the right format.
    comment = {
        ".py": "# TODO: model produced no usable content for: ",
        ".js": "// TODO: model produced no usable content for: ",
        ".css": "/* TODO: model produced no usable content for: ",
        ".sh": "# TODO: model produced no usable content for: ",
        ".sql": "-- TODO: model produced no usable content for: ",
    }.get(ext, "")
    if ext == ".css":
        return f"{comment}{user_request} */\n"
    if comment:
        return f"{comment}{user_request}\n"
    return f"{user_request}\n"


def _repair_html(salvage: str, user_request: str) -> str:
    lower = salvage.lower()
    if "<html" in lower or "<!doctype" in lower:
        # A real document that is merely truncated — close it.
        doc = _slice_html_document(salvage)
        low = doc.lower()
        if "</body>" not in low and "<body" in low:
            doc += "\n</body>"
        if "</html>" not in low:
            doc += "\n</html>"
        return doc
    if re.search(r"<\w+[^>]*>", salvage):
        body = salvage  # an HTML fragment — embed as-is
    elif salvage:
        body = "\n".join(
            f"  <p>{html_lib.escape(line)}</p>"
            for line in salvage.splitlines()
            if line.strip()
        )
    else:
        body = f"  <p>{html_lib.escape(user_request)}</p>"
    title = html_lib.escape(user_request[:60] or "Generated page")
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"ko\">\n"
        "<head>\n"
        "  <meta charset=\"utf-8\">\n"
        "  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"  <title>{title}</title>\n"
        "  <style>\n"
        "    body { font-family: system-ui, sans-serif; margin: 2rem auto; "
        "max-width: 720px; line-height: 1.6; padding: 0 1rem; }\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>"
    )


# ── filename inference ──────────────────────────────────────────────────

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


# ── orchestration ───────────────────────────────────────────────────────

async def generate_file_content(
    generate: Callable[[str], Awaitable[Any]],
    *,
    target_path: str,
    user_request: str,
    max_attempts: int = 2,
) -> Tuple[str, Dict[str, Any]]:
    """Generate validated file content with any LLM.

    ``generate`` is an async callable ``context -> raw model text``. Runs up
    to ``max_attempts`` model calls (the second with corrective feedback),
    then falls back to deterministic repair, so the returned content is
    always non-empty and structurally valid for the target type.
    """
    attempts: List[Dict[str, Any]] = []
    feedback: Optional[str] = None
    last_candidate = ""
    for attempt in range(1, max_attempts + 1):
        context = build_file_generation_context(target_path, user_request, feedback=feedback)
        try:
            raw = await generate(context)
        except Exception as exc:  # model backend hiccup — repair still delivers
            attempts.append({"attempt": attempt, "valid": False, "reason": f"generation error: {exc}"})
            feedback = "the model call failed"
            continue
        candidate = extract_file_content(str(raw or ""), target_path)
        ok, reason = validate_file_content(candidate, target_path)
        attempts.append({"attempt": attempt, "valid": ok, "reason": reason})
        if ok:
            return candidate, {"attempts": attempts, "repaired": False}
        if len(candidate) > len(last_candidate):
            last_candidate = candidate
        feedback = reason
    repaired = repair_file_content(last_candidate, target_path, user_request)
    return repaired, {"attempts": attempts, "repaired": True}


__all__ = [
    "build_file_generation_context",
    "extract_file_content",
    "generate_file_content",
    "infer_file_target",
    "looks_like_refusal",
    "repair_file_content",
    "validate_file_content",
]
