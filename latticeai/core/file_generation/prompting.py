"""Extension-aware generation instructions — step 1 of the pipeline.

Small models ignore abstract rules but reliably imitate concrete anchors, so
the prompt pins the exact first line of the expected output and the structural
rule for the target type. Multi-file bundles swap in the rule that links
sibling files instead of inlining everything.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .extraction import _ext

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


# Vite/React bundles need a module entry point, not classic script tags.
_BUNDLE_HTML_MODULE_RULE = (
    "Produce ONE complete HTML5 document: <!DOCTYPE html>, <html>, <head> with "
    "<meta charset=\"utf-8\"> and a <title>, and a closed </html> tag. "
    "This page is the Vite entry of a React project: the <body> must contain "
    "<div id=\"root\"></div> and load the app with "
    "<script type=\"module\" src=\"/src/main.jsx\"></script> just before "
    "</body>. No inline <style> blocks, no other scripts, no external files."
)


def _bundle_html_rule(bundle_files: List[str]) -> str:
    """Pick the HTML bundle rule that matches the bundle's technology."""
    if any(str(path).lower().endswith((".jsx", ".tsx")) for path in bundle_files):
        return _BUNDLE_HTML_MODULE_RULE
    return _BUNDLE_HTML_RULE


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
        type_rule = _bundle_html_rule(bundle_files)
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
