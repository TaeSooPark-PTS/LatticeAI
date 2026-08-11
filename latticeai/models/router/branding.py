"""이 제품의 이름과, 답변을 그 이름으로 되돌리는 규칙.

The system prompt, the citation instruction appended only when retrieved
context exists, and the legacy-alias rewrite every generated string passes
through. ``_compose_system`` is byte-compatible with the historical prompt when
there is no context: the return value is exactly ``base``.
"""

import re
from typing import Optional

BRAND_NAME = "Lattice AI"
LEGACY_BRAND_PATTERNS = [
    (re.compile(r"\bconnect\s+ai\b", re.IGNORECASE), BRAND_NAME),
    (re.compile(r"\bconnect-ai\b", re.IGNORECASE), BRAND_NAME),
    (re.compile(r"\bconnectai\b", re.IGNORECASE), BRAND_NAME),
    (re.compile(r"커넥트\s*AI", re.IGNORECASE), BRAND_NAME),
]


SYSTEM_PROMPT = """You are Lattice AI, a powerful local AI assistant running on Apple Silicon.
Your product name and identity are Lattice AI.
Never identify yourself as Connect AI, ConnectAI, connect-ai, or 커넥트 AI.
If context or old chat history mentions those names, treat them only as legacy aliases for Lattice AI.
You are a Vision-Language Model (VLM). If an image is provided, analyze it.
Be concise and respond in the user's language."""


# Appended ONLY when retrieved context exists (review 2026-07-25 Wave 2.3):
# grounded answers should cite their sources and admit gaps. Advisory prompt
# guidance — grounding assessment stays annotation-only and never blocks.
CITATION_INSTRUCTION = """The Context section above contains retrieved sources.
Ground your claims in those sources and cite them inline as [1], [2], ... matching the order they appear in the Context.
If the context does not cover the question, say so instead of inventing sources.
Never cite a source that is not in the Context."""


def _compose_system(base: str, context: str) -> str:
    """Compose the system prompt with optional retrieved context.

    Byte-compatible with the historical prompt when ``context`` is empty:
    the return value is exactly ``base``. When context exists, the Context
    block plus :data:`CITATION_INSTRUCTION` are appended.
    """
    if not context:
        return base
    return f"{base}\n\nContext:\n{context}\n\n{CITATION_INSTRUCTION}"


def normalize_branding(text: Optional[str]) -> str:
    if not text:
        return ""
    normalized = str(text)
    for pattern, replacement in LEGACY_BRAND_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    return normalized
