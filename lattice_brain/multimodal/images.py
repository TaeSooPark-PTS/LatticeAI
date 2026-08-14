"""A picture as a first-class memory: what can be observed about one file.

:func:`extract_image_facts` observes one file — size, OCR, caption, vector, each
with its own status — and answers with :class:`ImageFacts`. Turning those facts
into an ``Image`` node with its chunks, concepts and source link was the other
half of this module; ``lattice-core``'s graph write engine owns it since
v11.6.0, and this side never learned what a node was anyway.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..quiet import quiet
from .common import (
    MAX_INDEX_TEXT_CHARS,
    MAX_THUMBNAIL_CHARS,
    MODALITY_IMAGE,
    THUMBNAIL_EDGE,
)
from .ports import MultimodalPorts


@dataclass
class ImageFacts:
    """Everything observed about one image, and the status of each attempt."""

    path: str
    width: Optional[int] = None
    height: Optional[int] = None
    image_format: Optional[str] = None
    mode: Optional[str] = None
    #: ``ok`` | ``empty`` | ``unavailable`` | ``failed`` | ``skipped``
    ocr_status: str = "skipped"
    ocr_text: str = ""
    ocr_detail: str = ""
    #: ``ok`` | ``unavailable``
    caption_status: str = "unavailable"
    caption: Optional[str] = None
    #: ``ok`` | ``unavailable`` | ``failed``
    embedding_status: str = "unavailable"
    embedding: Optional[List[float]] = None
    embedding_detail: str = ""
    thumbnail: Optional[str] = None
    #: Set when the file could not be opened as an image at all.
    error: str = ""

    @property
    def readable(self) -> bool:
        return not self.error

    def index_text(self) -> str:
        """The text a search engine can actually match this image on."""
        parts = [part for part in (self.caption, self.ocr_text) if part]
        return "\n".join(parts).strip()[:MAX_INDEX_TEXT_CHARS]

    def as_metadata(self) -> Dict[str, Any]:
        """Flat, JSON-safe view stored on the graph node."""
        payload: Dict[str, Any] = {
            "modality": MODALITY_IMAGE,
            "width": self.width,
            "height": self.height,
            "format": self.image_format,
            "mode": self.mode,
            "ocr_status": self.ocr_status,
            "ocr_chars": len(self.ocr_text),
            "caption_status": self.caption_status,
            "vision_embedding": self.embedding_status,
        }
        if self.ocr_text:
            payload["ocr_text"] = self.ocr_text
        if self.ocr_detail:
            payload["ocr_detail"] = self.ocr_detail
        if self.caption:
            payload["caption"] = self.caption
        if self.embedding_detail:
            payload["vision_embedding_detail"] = self.embedding_detail
        if self.thumbnail:
            payload["thumbnail"] = self.thumbnail
        if self.error:
            payload["image_error"] = self.error
        return payload


def _open_image(path: str) -> Any:
    """Pillow's ``Image.open`` behind a guarded import."""
    from PIL import Image  # local import: keeps the module importable without it

    return Image.open(str(path))


def _thumbnail_data_uri(image: Any, edge: int = THUMBNAIL_EDGE) -> Optional[str]:
    """A tiny inline PNG the Evidence panel can render with no new route.

    Serving the original file would mean either a new static route over the
    user's disk or reusing ``/local/serve``, which exists precisely to make
    every read pass an explicit approval. A 96px data URI on the node dodges
    both: it is already inside the graph the user is looking at.
    """
    try:
        small = image.copy()
        small.thumbnail((edge, edge))
        if small.mode not in {"RGB", "L"}:
            small = small.convert("RGB")
        buffer = io.BytesIO()
        small.save(buffer, format="PNG")
    except Exception:  # noqa: BLE001 — a missing thumbnail is not a failed ingest
        quiet()
        return None
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    if len(encoded) > MAX_THUMBNAIL_CHARS:
        return None
    return f"data:image/png;base64,{encoded}"


def _run_ocr(image: Any) -> Dict[str, str]:
    """OCR through ``pytesseract`` when it is installed, honestly otherwise."""
    try:
        import pytesseract  # optional local binary + wrapper
    except Exception as exc:  # noqa: BLE001 — absence is a state, not an error
        return {"status": "unavailable", "text": "", "detail": str(exc)}
    try:
        text = str(pytesseract.image_to_string(image) or "").strip()
    except Exception as exc:  # noqa: BLE001 — a broken OCR runtime is a state
        return {"status": "failed", "text": "", "detail": str(exc)}
    if not text:
        return {"status": "empty", "text": "", "detail": "no text found in the image"}
    return {"status": "ok", "text": text[:MAX_INDEX_TEXT_CHARS], "detail": ""}


def extract_image_facts(
    path: str,
    *,
    ports: Optional[MultimodalPorts] = None,
    ocr: bool = True,
    thumbnail: bool = True,
) -> ImageFacts:
    """Observe one image: size, OCR, caption, vector — each with its status.

    Never raises. An unreadable file returns ``ImageFacts(error=...)`` so the
    caller can record "we saw this file and could not read it" instead of
    losing the memory entirely.
    """
    ports = ports or MultimodalPorts()
    facts = ImageFacts(path=str(path))
    try:
        with _open_image(path) as image:
            facts.width = int(image.width)
            facts.height = int(image.height)
            facts.image_format = image.format
            facts.mode = image.mode
            if ocr:
                result = _run_ocr(image)
                facts.ocr_status = result["status"]
                facts.ocr_text = result["text"]
                facts.ocr_detail = result["detail"]
            if thumbnail:
                facts.thumbnail = _thumbnail_data_uri(image)
    except Exception as exc:  # noqa: BLE001 — an unreadable image is a state
        facts.error = str(exc)
        return facts

    if ports.captioner is not None:
        caption = _safe_caption(ports.captioner, facts.path)
        if caption:
            facts.caption = caption
            facts.caption_status = "ok"

    if ports.vision_embedder is not None:
        _apply_vision_embedding(facts, ports.vision_embedder)
    return facts


def _safe_caption(
    captioner: Callable[[str], Optional[str]], path: str
) -> Optional[str]:
    """Ask the VLM; a failure means *no caption*, never an invented one."""
    try:
        caption = captioner(path)
    except Exception:  # noqa: BLE001 — a broken captioner must not fail an ingest
        quiet()
        return None
    cleaned = str(caption or "").strip()
    return cleaned or None


def _apply_vision_embedding(
    facts: ImageFacts, embedder: Callable[[str], List[float]]
) -> None:
    try:
        vector = [float(value) for value in embedder(facts.path)]
    except Exception as exc:  # noqa: BLE001 — an absent model is not a failed ingest
        facts.embedding_status = "failed"
        facts.embedding_detail = str(exc)
        return
    if not vector:
        facts.embedding_status = "failed"
        facts.embedding_detail = "vision provider returned an empty vector"
        return
    facts.embedding = vector
    facts.embedding_status = "ok"


# ── extraction quality for pictures ──────────────────────────────────────────
def image_quality_score(facts: ImageFacts) -> Dict[str, Any]:
    """``{"score": float, "reasons": [...]}`` for an image memory.

    Deliberately *not* a judgement about the photograph: it scores how much of
    this image the Brain can actually retrieve later. Pixels alone are worth
    little to a text query, OCR text is worth the most, a caption is worth a
    lot, and a vector is worth something even without either.
    """
    if not facts.readable:
        return {"score": 0.0, "reasons": ["image_unreadable"]}
    reasons: List[str] = []
    score = 0.15  # we know it is an image and how big it is
    if facts.ocr_text:
        # 400+ characters of recognized text is a page, not a label.
        score += 0.45 * min(1.0, len(facts.ocr_text) / 400.0)
        reasons.append("ocr_text")
    elif facts.ocr_status == "unavailable":
        reasons.append("ocr_unavailable")
    elif facts.ocr_status == "skipped":
        reasons.append("ocr_skipped")
    else:
        reasons.append("no_ocr_text")
    if facts.caption:
        score += 0.3
        reasons.append("vision_caption")
    else:
        reasons.append("no_vision_caption")
    if facts.embedding_status == "ok":
        score += 0.1
        reasons.append("vision_embedding")
    return {"score": round(max(0.0, min(1.0, score)), 4), "reasons": reasons}


