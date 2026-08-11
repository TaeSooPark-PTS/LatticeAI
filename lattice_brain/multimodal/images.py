"""A picture as a first-class memory: what was observed, and what was written.

Two halves, in order: :func:`extract_image_facts` observes one file (size, OCR,
caption, vector — each with its own status), and :func:`write_image_memory`
turns those facts into an ``Image`` node with its chunks, concepts and source
link. Video reuses both verbatim for its keyframes, which is why nothing here
knows what a video is.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..quiet import quiet
from ..utils import utc_now_iso
from .common import (
    MAX_INDEX_TEXT_CHARS,
    MAX_THUMBNAIL_CHARS,
    MODALITY_IMAGE,
    SUMMARY_CHARS,
    THUMBNAIL_EDGE,
    _sha256_file,
    _sha256_text,
    _split_index_text,
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


def image_node_id(content_hash: str, workspace_id: Optional[str] = None) -> str:
    """Workspace-scoped, content-addressed id — re-ingesting is idempotent."""
    scoped = f"{workspace_id or 'legacy-global'}|{content_hash}"
    return f"image:{_sha256_text(scoped)[:24]}"


def write_image_memory(
    store: Any,
    *,
    path: Path,
    facts: ImageFacts,
    title: str,
    source_type: str = MODALITY_IMAGE,
    source_uri: Optional[str] = None,
    owner: Optional[str] = None,
    workspace_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    captured_at: Optional[str] = None,
    modified_at: Optional[str] = None,
    permissions: Optional[Dict[str, Any]] = None,
    extra_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write one ``Image`` node (plus ``ImageText``/chunks) into the graph.

    The node is the image itself rather than a ``Document`` that happens to be
    a picture, because that is what the rest of the product reasons about: the
    graph already declares ``Image``/``ImageText``/``CONTAINS_IMAGE``, and
    ``hybrid_search`` already ranks ``Image`` as a first-class result type.

    Uses the same cross-mixin write door (``_upsert_node``/``_upsert_edge``/
    ``_upsert_chunk``) that every other ingest path uses — the store exposes no
    public node writer, and inventing a second one for images would be a
    parallel write path to keep in sync forever.
    """
    captured_at = captured_at or utc_now_iso()
    content_hash = _sha256_file(path)
    node_id = image_node_id(content_hash, workspace_id)
    index_text = facts.index_text()
    metadata: Dict[str, Any] = {
        "filename": path.name,
        "file_path": str(path),
        "ext": path.suffix.lower(),
        "bytes": path.stat().st_size,
        "sha256": content_hash,
        "content_hash": content_hash,
        "source_type": source_type,
        "source_uri": source_uri or str(path),
        "captured_at": captured_at,
        "modified_at": modified_at,
        "owner": owner,
        "workspace_id": workspace_id,
        "permissions": permissions or {},
        "conversation_id": conversation_id,
        **facts.as_metadata(),
        **(extra_metadata or {}),
    }
    # Honest summary: when nothing could be read out of the picture, say so
    # rather than leaving a blank card that looks like a failed render.
    summary = index_text[:SUMMARY_CHARS] or f"[{MODALITY_IMAGE}] {path.name}"
    chunk_ids: List[str] = []

    with store._connect() as conn:
        duplicate = (
            conn.execute("SELECT 1 FROM nodes WHERE id=? LIMIT 1", (node_id,)).fetchone()
            is not None
        )
        store._upsert_node(
            conn,
            node_id,
            "Image",
            title or path.name,
            summary=summary,
            metadata=metadata,
            raw=metadata,
            owner=owner,
            workspace_id=workspace_id,
        )
        if facts.ocr_text:
            image_text_id = f"imagetext:{_sha256_text(f'{node_id}:ocr')[:24]}"
            store._upsert_node(
                conn,
                image_text_id,
                "ImageText",
                f"{path.name} OCR",
                summary=facts.ocr_text[:700],
                metadata={
                    "source_node": node_id,
                    "chars": len(facts.ocr_text),
                    "workspace_id": workspace_id,
                },
                owner=owner,
                workspace_id=workspace_id,
            )
            store._upsert_edge(
                conn,
                node_id,
                image_text_id,
                "포함함",
                weight=0.8,
                metadata={"source": "ocr", "workspace_id": workspace_id},
            )
        for index, piece in enumerate(_split_index_text(index_text)):
            chunk_id = f"chunk:{_sha256_text(f'{node_id}:{index}:{piece}')[:24]}"
            chunk_ids.append(chunk_id)
            chunk_meta = {
                "index": index,
                "source_node": node_id,
                "workspace_id": workspace_id,
                "modality": MODALITY_IMAGE,
            }
            store._upsert_node(
                conn,
                chunk_id,
                "Chunk",
                f"{path.name} chunk {index + 1}",
                summary=piece[:SUMMARY_CHARS],
                metadata=chunk_meta,
                owner=owner,
                workspace_id=workspace_id,
            )
            store._upsert_chunk(
                conn,
                chunk_id=chunk_id,
                source_node=node_id,
                text=piece,
                metadata=chunk_meta,
            )
            store._upsert_edge(conn, node_id, chunk_id, "포함함")
        # Concepts come from what is *in* the picture, never from its name:
        # "IMG_2381" is not a topic, and turning filenames into concept nodes
        # would fill the graph with hubs that mean nothing.
        concept_ids = _attach_concepts(
            store,
            conn,
            node_id=node_id,
            text=index_text,
            owner=owner,
            workspace_id=workspace_id,
        )
        source_node_id = _attach_source(
            store,
            conn,
            node_id=node_id,
            source_type=source_type,
            source_uri=source_uri or str(path),
            title=title or path.name,
            content_hash=content_hash,
            captured_at=captured_at,
            owner=owner,
            workspace_id=workspace_id,
        )
    metadata["concepts"] = concept_ids
    return {
        "node_id": node_id,
        "type": "Image",
        "title": title or path.name,
        "sha256": content_hash,
        "content_hash": content_hash,
        "source_node_id": source_node_id,
        "chunk_ids": chunk_ids,
        "chunk_count": len(chunk_ids),
        "duplicate": duplicate,
        "captured_at": captured_at,
        "metadata": metadata,
    }


def _attach_concepts(
    store: Any,
    conn: Any,
    *,
    node_id: str,
    text: str,
    owner: Optional[str],
    workspace_id: Optional[str],
) -> List[str]:
    """Pull concepts out of the caption/OCR so the picture joins the graph.

    This is what "the caption contributes to the graph" means concretely: the
    same extractor every text door uses, over the same text, producing the same
    ``Concept``/``Feature``/… nodes and ``포함함`` edges — so a photo of a
    whiteboard about Q3 planning is one hop from every note about Q3 planning,
    instead of being an island that only exact search can reach.

    An image with nothing readable in it yields no concepts, which is correct:
    there is nothing to say about it.
    """
    body = str(text or "").strip()
    if not body:
        return []
    from ..graph._kg_common import (  # local: keeps this module's import light
        _classify_node_type,
        _extract_concepts,
    )

    # The id derivation is imported rather than re-derived: two copies of a
    # node-id rule diverge, and a diverged id is a duplicate concept nobody
    # can see.
    from ..graph.ingest import _scoped_slug_id

    concept_ids: List[str] = []
    for concept in _extract_concepts(body, limit=10):
        node_type = _classify_node_type(concept, body)
        concept_id = _scoped_slug_id(node_type.lower(), concept, workspace_id)
        store._upsert_node(
            conn,
            concept_id,
            node_type,
            concept,
            metadata={
                "auto_extracted": True,
                "source_node": node_id,
                "modality": MODALITY_IMAGE,
                "workspace_id": workspace_id,
            },
            owner=owner,
            workspace_id=workspace_id,
        )
        store._upsert_edge(conn, node_id, concept_id, "포함함", weight=0.8)
        concept_ids.append(concept_id)
    return concept_ids


def _attach_source(
    store: Any,
    conn: Any,
    *,
    node_id: str,
    source_type: str,
    source_uri: str,
    title: str,
    content_hash: str,
    captured_at: str,
    owner: Optional[str],
    workspace_id: Optional[str],
) -> Optional[str]:
    """Link the image to a ``Source`` node when the store can make one."""
    attach = getattr(store, "_attach_source_node", None)
    if not callable(attach):
        return None
    return str(
        attach(
            conn,
            node_id,
            source_type=source_type,
            source_uri=source_uri,
            title=title,
            content_hash=content_hash,
            captured_at=captured_at,
            extra={"owner": owner, "workspace_id": workspace_id, "modality": MODALITY_IMAGE},
        )
    )
