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

import ast
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
            continue
        if best is None or len(candidate) > len(best):
            best = candidate
    return best


# ── validation ──────────────────────────────────────────────────────────

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
    ".jsx": "Produce one complete React component file in JSX.",
    ".ts": "Produce complete valid TypeScript source code.",
    ".tsx": "Produce one complete React component file in TSX (TypeScript).",
    ".vue": "Produce ONE complete Vue single-file component with closed <template>/<script>/<style> blocks.",
    ".svelte": "Produce ONE complete Svelte component; every <script>/<style> block must be closed.",
}

# Multi-file bundles override the standalone-HTML rule: the page must link
# its sibling files instead of inlining everything.
_BUNDLE_HTML_RULE = (
    "Produce ONE complete HTML5 document: <!DOCTYPE html>, <html>, <head> with "
    "<meta charset=\"utf-8\"> and a <title>, and a closed </html> tag. "
    "This page is part of a multi-file project: link the project stylesheet(s) "
    "with <link rel=\"stylesheet\" href=\"...\"> and load the project script(s) "
    "with <script src=\"...\"></script> just before </body>. Reference ONLY the "
    "project files listed below — no other external files, no inline <style> "
    "blocks, no inline behavior scripts."
)


def build_file_generation_context(
    target_path: str,
    user_request: str,
    feedback: Optional[str] = None,
    bundle_files: Optional[List[str]] = None,
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
    if bundle_files and ext in (".html", ".htm"):
        type_rule = _BUNDLE_HTML_RULE
    if type_rule:
        parts.append(f"- {type_rule}")
    if bundle_files:
        listed = ", ".join(bundle_files)
        parts.append(f"- Project files in this bundle: {listed}")
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
    if ext == ".py" and salvage:
        # The repair guarantee for Python is parseability: unparseable output
        # is preserved honestly as a commented-out draft, never as a broken
        # module the user has to debug.
        try:
            ast.parse(salvage)
            return salvage
        except SyntaxError:
            commented = "\n".join(f"# {line}" for line in salvage.splitlines())
            return (
                f"# TODO: model produced invalid Python for: {user_request}\n"
                "# The draft below is preserved as comments — fix and uncomment.\n"
                f"{commented}\n"
            )
    if salvage:
        return salvage
    # Nothing usable at all — leave an honest placeholder in the right format.
    comment = {
        ".py": "# TODO: model produced no usable content for: ",
        ".js": "// TODO: model produced no usable content for: ",
        ".jsx": "// TODO: model produced no usable content for: ",
        ".ts": "// TODO: model produced no usable content for: ",
        ".tsx": "// TODO: model produced no usable content for: ",
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


# Extensions the Brain UI can render inline (preview) after creation.
PREVIEWABLE_EXTENSIONS = frozenset({
    ".html", ".htm", ".md", ".markdown", ".txt", ".json", ".css", ".js",
    ".csv", ".py", ".yaml", ".yml", ".xml", ".sql", ".sh",
    ".jsx", ".ts", ".tsx", ".vue", ".svelte",
})


# ── write-side sanitize (ArtifactWritePipeline) ─────────────────────────

def sanitize_write_content(
    target_path: str,
    content: Any,
    user_request: str = "",
) -> Tuple[str, Dict[str, Any]]:
    """Single write-side guarantee for model-produced file content.

    The direct chat path already runs the full generate→validate→repair
    pipeline, but the agent JSON loop historically wrote ``args.content``
    verbatim — weak models routinely put fenced/chatty payloads there. This
    conservative sanitizer closes that gap for *any* write entry point:

    1. content that already validates is returned byte-for-byte unchanged
       (trusted/user-authored content is never mangled);
    2. otherwise the extraction pass strips fences/think-blocks/chat noise
       and is used only when the extracted payload validates;
    3. otherwise deterministic repair guarantees a structurally valid file.

    Empty content is left untouched (creating an empty file is a legitimate,
    intentional action — e.g. ``__init__.py``). Returns ``(content, meta)``
    where meta is ``{"sanitized": bool, "repaired": bool, "reason": str}``.
    """
    raw = str(content or "")
    if not raw.strip():
        return raw, {"sanitized": False, "repaired": False, "reason": "empty"}
    ok, reason = validate_file_content(raw, target_path)
    if ok:
        return raw, {"sanitized": False, "repaired": False, "reason": "ok"}
    extracted = extract_file_content(raw, target_path)
    if extracted:
        extracted_ok, _ = validate_file_content(extracted, target_path)
        if extracted_ok:
            return extracted, {"sanitized": True, "repaired": False, "reason": reason}
    repaired = repair_file_content(
        extracted or raw, target_path, user_request or f"content for {target_path}"
    )
    return repaired, {"sanitized": True, "repaired": True, "reason": reason}


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


# ── project manifest (multi-file bundles) ───────────────────────────────

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


def infer_project_manifest(message: str) -> Optional[Dict[str, Any]]:
    """Infer a multi-file web-project manifest from a creation request.

    "todo 앱 html+css+js로 만들어줘" should yield real linked files, not one
    inlined page. Deliberately narrow and deterministic (weak local models
    never see this decision): requires a creation verb, an HTML-page intent,
    at least one *additional* named technology (css/js), and no explicit
    filename. Single-type requests return ``None`` so the existing
    single-file flow is completely unchanged.
    """
    text = (message or "").strip()
    if not text or not _CREATE_VERB_RE.search(text):
        return None
    if _EXPLICIT_FILENAME_RE.search(text):
        return None
    lower = text.lower()
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


_HTML_LOCAL_REF_RE = re.compile(
    r"(?:href|src)\s*=\s*[\"']([^\"'#?]+)[\"']", re.IGNORECASE
)
_EXTERNAL_REF_PREFIXES = ("http://", "https://", "//", "data:", "mailto:", "tel:", "javascript:")


def _local_bundle_refs(html: str) -> List[str]:
    """File references inside an HTML document that must exist in the bundle."""
    refs: List[str] = []
    for ref in _HTML_LOCAL_REF_RE.findall(html or ""):
        candidate = ref.strip()
        if not candidate or candidate.startswith(_EXTERNAL_REF_PREFIXES):
            continue
        if "." not in candidate.rsplit("/", 1)[-1]:
            continue  # anchors / routes, not files
        refs.append(candidate)
    return refs


def repair_bundle_references(files: Dict[str, str]) -> Tuple[Dict[str, str], List[str]]:
    """Deterministically point dangling HTML refs at real bundle files.

    A weak model asked for ``style.css`` sometimes links ``styles.css``. When
    a referenced file is missing but the bundle contains exactly one file of
    the same extension, the reference is rewritten. Returns ``(files, fixes)``.
    """
    names = {p.rsplit("/", 1)[-1] for p in files}
    fixes: List[str] = []
    repaired = dict(files)
    for path, content in files.items():
        if _ext(path) not in (".html", ".htm"):
            continue
        updated = content
        for ref in _local_bundle_refs(content):
            base = ref.rsplit("/", 1)[-1]
            if base in names:
                continue
            same_ext = [n for n in names if _ext(n) == _ext(base)]
            if len(same_ext) == 1:
                updated = updated.replace(ref, same_ext[0])
                fixes.append(f"{path}: '{ref}' -> '{same_ext[0]}'")
        if updated != content:
            repaired[path] = updated
    return repaired, fixes


def validate_project_bundle(files: Dict[str, str]) -> Dict[str, Any]:
    """Bundle-level verification: every file valid, every HTML ref resolvable."""
    issues: List[str] = []
    per_file: Dict[str, Dict[str, Any]] = {}
    names = {p.rsplit("/", 1)[-1] for p in files}
    for path, content in files.items():
        ok, reason = validate_file_content(content, path)
        per_file[path] = {"valid": ok, "reason": reason}
        if not ok:
            issues.append(f"{path}: {reason}")
        if _ext(path) in (".html", ".htm"):
            for ref in _local_bundle_refs(content):
                if ref.rsplit("/", 1)[-1] not in names:
                    issues.append(f"{path}: references missing file '{ref}'")
    return {"ok": not issues, "issues": issues, "files": per_file}


# ── orchestration ───────────────────────────────────────────────────────

async def generate_file_content(
    generate: Callable[[str], Awaitable[Any]],
    *,
    target_path: str,
    user_request: str,
    max_attempts: int = 2,
    bundle_files: Optional[List[str]] = None,
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
        context = build_file_generation_context(
            target_path, user_request, feedback=feedback, bundle_files=bundle_files,
        )
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
    "PREVIEWABLE_EXTENSIONS",
    "build_file_generation_context",
    "extract_file_content",
    "generate_file_content",
    "infer_file_target",
    "infer_project_manifest",
    "looks_like_refusal",
    "repair_bundle_references",
    "repair_file_content",
    "sanitize_write_content",
    "validate_file_content",
    "validate_project_bundle",
]
