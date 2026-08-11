"""Deterministic scaffolds — step 5, the last resort after the retries.

The user asked for a file, so the request must still end in a well-formed file
of the target type, never an error. Nothing here calls a model: whatever the
model did produce is salvaged where it can be, and honestly labelled where it
cannot.
"""

from __future__ import annotations

import ast
import html as html_lib
import json
import re

from .extraction import _ext, _slice_html_document, _slice_json_document
from .validation import looks_like_refusal


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
