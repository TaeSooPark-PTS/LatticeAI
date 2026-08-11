"""The single write-side guarantee for model-produced file content.

``sanitize_write_content`` is the one door every write entry point goes
through — the direct chat path, the agent JSON loop, the tools layer — so a
fenced or chatty payload can never be persisted verbatim no matter which
surface produced it.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from .extraction import extract_file_content
from .repair import repair_file_content
from .validation import validate_file_content

# Extensions the Brain UI can render inline (preview) after creation.
PREVIEWABLE_EXTENSIONS = frozenset({
    ".html", ".htm", ".md", ".markdown", ".txt", ".json", ".css", ".js",
    ".csv", ".py", ".yaml", ".yml", ".xml", ".sql", ".sh",
    ".jsx", ".ts", ".tsx", ".vue", ".svelte",
})


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
