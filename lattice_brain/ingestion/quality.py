"""Advisory extraction-quality scoring, and the capture CTA built on it.

Pure heuristics over already-extracted text — no model call, no network, and
deterministic. The score never blocks an ingest; it annotates the result so a
capture surface can say "this capture is thin" and offer a way to fix it
instead of silently storing junk.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── Extraction quality heuristics (v9.8.0 A1) ────────────────────────────────
# Pure heuristics over the extracted text — no model calls, no network. The
# score is *advisory*: it never blocks an ingest, it only annotates the result
# so capture surfaces (browser, folder scan) can surface low-quality warnings.
QUALITY_HIGH_THRESHOLD = 0.7
QUALITY_LOW_THRESHOLD = 0.4
QUALITY_LOW_WARNING = "추출 품질이 낮습니다 — 원문 확인을 권장합니다."
_WEB_SOURCE_TYPES = frozenset({"web_url", "browser_tab"})
# Standalone short lines that smell like leftover site chrome (nav/menu/footer).
_BOILERPLATE_LINE_MARKERS = frozenset(
    {
        "home", "menu", "nav", "navigation", "login", "log in", "sign in",
        "sign up", "register", "subscribe", "search", "about", "about us",
        "contact", "contact us", "privacy policy", "terms of service",
        "cookie policy", "accept cookies", "accept all cookies", "share",
        "skip to content", "copyright", "all rights reserved", "sitemap",
        "back to top", "footer", "read more", "next", "previous",
    }
)


def _quality_level(score: float) -> str:
    if score >= QUALITY_HIGH_THRESHOLD:
        return "high"
    if score >= QUALITY_LOW_THRESHOLD:
        return "medium"
    return "low"


def assess_extraction_quality(
    text: Optional[str],
    *,
    source_type: Optional[str] = None,
    upstream_confidence: Optional[Any] = None,
) -> Dict[str, Any]:
    """Score extracted text 0..1 with reasons (pure heuristic, deterministic).

    Signals: text length, whitespace ratio, character/word diversity
    (repetition), sentence structure, and — for web sources — leftover
    nav/menu boilerplate. When the upstream extractor supplies its own
    confidence (``upstream_confidence``), that value wins verbatim: the
    extractor saw the raw document, this function only sees its output.
    """
    if upstream_confidence is not None:
        try:
            score = max(0.0, min(1.0, float(upstream_confidence)))
        except (TypeError, ValueError):
            score = None
        if score is not None:
            return {
                "score": round(score, 4),
                "level": _quality_level(score),
                "reasons": ["upstream_confidence"],
            }

    raw = str(text or "")
    stripped = raw.strip()
    if not stripped:
        return {"score": 0.0, "level": "low", "reasons": ["empty_text"]}

    reasons: List[str] = []
    length = len(stripped)
    sample = stripped[:4000]
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    words = stripped.split()

    # 1) Length — very short extractions rarely carry recall value.
    if length < 40:
        length_factor = 0.35
        reasons.append("very_short_text")
    elif length < 120:
        length_factor = 0.6
        reasons.append("short_text")
    elif length < 300:
        length_factor = 0.85
    else:
        length_factor = 1.0

    # 2) Sentence structure — prose has sentence-ending punctuation.
    sentence_marks = sum(sample.count(mark) for mark in (".", "!", "?", "…", "。", "！", "？"))
    if sentence_marks > 0:
        structure_factor = 1.0
    elif length < 200:
        structure_factor = 0.75  # titles/snippets legitimately lack periods
    else:
        structure_factor = 0.45
        reasons.append("no_sentence_structure")

    # 3) Diversity — repeated characters/lines/words indicate extraction junk.
    diversity_factor = 1.0
    distinct_chars = len(set(sample.lower()))
    if distinct_chars < 10:
        diversity_factor *= 0.2
        reasons.append("low_character_diversity")
    elif distinct_chars < 20:
        diversity_factor *= 0.7
    if len(lines) >= 6:
        top_count = max(lines.count(ln) for ln in set(lines))
        if top_count >= max(3, len(lines) // 4):
            diversity_factor *= 0.5
            reasons.append("repetitive_lines")
    if len(words) >= 30 and (len(set(w.lower() for w in words)) / len(words)) < 0.25:
        diversity_factor *= 0.5
        reasons.append("repetitive_words")

    # 4) Cleanliness — whitespace floods, fragmented lines, site chrome.
    cleanliness_factor = 1.0
    whitespace_ratio = sum(1 for ch in raw if ch.isspace()) / max(1, len(raw))
    if whitespace_ratio > 0.45:
        cleanliness_factor *= 0.6
        reasons.append("high_whitespace_ratio")
    if len(lines) >= 8:
        short_lines = sum(1 for ln in lines if len(ln.split()) <= 3)
        if short_lines / len(lines) > 0.6:
            cleanliness_factor *= 0.6
            reasons.append("fragmented_lines")
    boilerplate_hits = sum(
        1 for ln in lines if ln.lower().strip(" .:>|•·-–—*") in _BOILERPLATE_LINE_MARKERS
    )
    if lines and boilerplate_hits >= 3 and (boilerplate_hits / len(lines)) > 0.2:
        cleanliness_factor *= 0.35
        if str(source_type or "").lower() in _WEB_SOURCE_TYPES:
            reasons.append("nav_menu_remnants")
        else:
            reasons.append("boilerplate_markers")

    score = length_factor * structure_factor * diversity_factor * cleanliness_factor
    score = max(0.0, min(1.0, score))
    if not reasons:
        reasons.append("clean_extraction")
    return {"score": round(score, 4), "level": _quality_level(score), "reasons": reasons}


# ── capture quality CTA (backlog #9, review §7.2 C) ──────────────────────────
# Structured verdict over the same extraction-quality schema the rest of the
# pipeline uses, so capture surfaces (browser extension, read-url) can render
# an honest "this capture is thin" CTA instead of silently storing junk.
CAPTURE_SUGGESTIONS_THIN = ["recapture", "paste_manually", "highlight_source"]
_CAPTURE_REASON_LABELS = {
    "empty_text": "추출된 본문이 비어 있습니다",
    "very_short_text": "추출된 본문이 매우 짧습니다",
    "short_text": "추출된 본문이 짧습니다",
    "no_sentence_structure": "문장 구조가 거의 없습니다",
    "low_character_diversity": "반복 문자가 대부분입니다",
    "repetitive_lines": "같은 줄이 반복됩니다",
    "repetitive_words": "같은 단어가 반복됩니다",
    "high_whitespace_ratio": "공백이 지나치게 많습니다",
    "fragmented_lines": "줄이 잘게 조각나 있습니다",
    "nav_menu_remnants": "메뉴/내비게이션 잔여물이 많습니다",
    "boilerplate_markers": "상용구 텍스트가 많습니다",
    "no_extracted_text": "추출된 텍스트가 없습니다",
}


def capture_quality_verdict(
    extraction_quality: Optional[Dict[str, Any]],
    *,
    source_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Structured CTA verdict from a pipeline ``extraction_quality`` dict.

    ``{"status": "thin"|"ok", "reason": str|None, "suggestions": [...],
    "score": float|None, "level": str|None}``. ``thin`` (level == "low", the
    same threshold as the ingest warning) carries actionable suggestions —
    ``recapture`` / ``paste_manually`` / ``highlight_source`` — so the UI can
    offer the user a way to fix the capture instead of hiding the problem.
    Deterministic and never raises; ``None`` input yields an honest ``thin``.
    """
    if not isinstance(extraction_quality, dict):
        return {
            "status": "thin",
            "reason": _CAPTURE_REASON_LABELS["no_extracted_text"],
            "reason_codes": ["no_extracted_text"],
            "suggestions": list(CAPTURE_SUGGESTIONS_THIN),
            "score": None,
            "level": None,
        }
    level = str(extraction_quality.get("level") or "")
    score = extraction_quality.get("score")
    reasons = [str(item) for item in (extraction_quality.get("reasons") or [])]
    thin = level == "low"
    reason = None
    if thin:
        labeled = [
            _CAPTURE_REASON_LABELS[code]
            for code in reasons
            if code in _CAPTURE_REASON_LABELS
        ]
        reason = "; ".join(labeled) if labeled else QUALITY_LOW_WARNING
    return {
        "status": "thin" if thin else "ok",
        "reason": reason,
        "reason_codes": reasons if thin else [],
        "suggestions": list(CAPTURE_SUGGESTIONS_THIN) if thin else [],
        "score": score,
        "level": level or None,
    }
