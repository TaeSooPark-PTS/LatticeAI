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


CITATION_INSTRUCTION = """The Context section above contains retrieved sources.
Ground your claims in those sources and cite them inline as [1], [2], ... matching the order they appear in the Context.
If the context does not cover the question, say so instead of inventing sources.
Never cite a source that is not in the Context."""


def _compose_system(base: str, context: str) -> str:
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
