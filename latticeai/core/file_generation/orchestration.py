"""Prompt → extract → validate → retry → repair, in that order.

The pipeline's driver. ``generate_file_content`` is the only place the model
is actually called; every other module here is pure. The salvage score decides
which rejected candidate repair gets to work from — closest to a file, not
longest.
"""

from __future__ import annotations

import ast
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .extraction import _ext, _slice_json_document, extract_file_content
from .prompting import build_file_generation_context
from .repair import repair_file_content
from .validation import (
    _BRACED_CODE_EXTENSIONS,
    _COMPONENT_EXTENSIONS,
    _check_balanced_delimiters,
    _check_component_blocks,
    looks_like_refusal,
    validate_file_content,
)


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
    to ``max_attempts`` model calls (each retry carrying corrective feedback),
    then falls back to deterministic repair, so the returned content is
    always non-empty and structurally valid for the target type.

    One extra call beyond ``max_attempts`` is spent — at most once per
    request — when the model has returned a byte-identical rejected reply.
    That is the one case where the ordinary retry is known to be dead on
    arrival: the corrective feedback did not change the reply, so the budget
    is better spent on a prompt that names the repetition than on a third
    identical round trip. Small local models hit this constantly; large ones
    never do, so the extra call is not charged to models that do not need it.
    """
    attempts: List[Dict[str, Any]] = []
    feedback: Optional[str] = None
    best_candidate = ""
    best_score = (-1, -1)
    seen: set[str] = set()
    escalations_left = 1
    attempt = 0
    budget = max_attempts
    while attempt < budget:
        attempt += 1
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
        record: Dict[str, Any] = {"attempt": attempt, "valid": ok, "reason": reason}
        if ok:
            attempts.append(record)
            return candidate, {"attempts": attempts, "repaired": False}

        # A small model handed the same corrective feedback often replays the
        # same reply verbatim. Saying "you sent this before" is the only signal
        # left that has any chance of moving it, and it makes the wasted retry
        # visible in the trace instead of looking like two genuine tries.
        fingerprint = candidate.strip()
        repeated = fingerprint in seen and bool(fingerprint)
        record["repeated"] = repeated
        seen.add(fingerprint)
        if repeated and escalations_left and attempt >= budget:
            # The retry budget is exhausted and the last thing it bought was a
            # duplicate. Buy one more, but only with a prompt that says so.
            escalations_left -= 1
            budget += 1
            record["escalated"] = True
        attempts.append(record)

        # Keep the candidate that is *closest to a file*, not the longest one.
        # Longest-wins handed repair a 900-character apology in preference to a
        # 300-character HTML document that only needed its </html> closing —
        # and repair can finish the document but can only bury the apology.
        score = _salvage_score(candidate, target_path)
        if score > best_score:
            best_score, best_candidate = score, candidate

        feedback = (
            f"{reason}. You already sent exactly this reply and it was rejected "
            "for the same reason — do not repeat it. Output the file itself, "
            "starting at its first character."
            if repeated else reason
        )
    repaired = repair_file_content(best_candidate, target_path, user_request)
    return repaired, {"attempts": attempts, "repaired": True}


def _salvage_score(candidate: str, target_path: str) -> Tuple[int, int]:
    """How useful an invalid candidate is as raw material for repair.

    ``(tier, length)`` — tier first, so a short real document always beats a
    long non-document; length breaks ties within a tier.

    Tier 2  something of the right shape that repair can finish (an HTML
            document missing its close tag, parseable-ish JSON, Python that
            at least tokenises).
    Tier 1  ordinary text: no structure, but the words may be the content.
    Tier 0  a refusal — repair should prefer literally anything else, because
            an apology written into the file is worse than an empty stub.
    """
    text = candidate.strip()
    if not text:
        return (0, 0)
    if looks_like_refusal(text):
        return (0, len(text))

    ext = _ext(target_path)
    lower = text.lower()
    if ext in (".html", ".htm"):
        if lower.startswith("<!doctype") or lower.startswith("<html"):
            return (2, len(text))
    elif ext == ".json":
        if _slice_json_document(text) is not None:
            return (2, len(text))
    elif ext == ".py":
        try:
            ast.parse(text)
        except SyntaxError:
            pass
        else:
            return (2, len(text))
    elif ext in _BRACED_CODE_EXTENSIONS:
        if _check_balanced_delimiters(text)[0]:
            return (2, len(text))
    elif ext in _COMPONENT_EXTENSIONS:
        if _check_component_blocks(text)[0]:
            return (2, len(text))
    elif ext == ".css" and "{" in text and "}" in text:
        return (2, len(text))
    return (1, len(text))
