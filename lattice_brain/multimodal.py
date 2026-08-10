"""Images and audio as first-class memories (v11.1.0, Track 3).

Before this module the Brain could only remember things that arrived as text.
A screenshot of a whiteboard, a photo of a receipt, a voice memo — all of them
either bounced off the ingestion pipeline or landed as an opaque ``Document``
node whose only searchable content was its filename.

What routes here
----------------
:func:`detect_modality` reads the MIME type first and the extension second, and
answers with one of ``text`` / ``image`` / ``audio`` / ``video``. ``video`` is
deliberately a *recognized but unsupported* answer in this release: keyframe
extraction needs a decoder this project does not ship, and returning "video,
out of scope" is worth more than pretending a ``.mov`` is a picture.

What an image memory contains
-----------------------------
:func:`extract_image_facts` gathers only what it can actually observe:

* **dimensions/format** from Pillow (a core dependency);
* **ocr_text** from ``pytesseract`` when it is installed — otherwise
  ``ocr_status="unavailable"`` and no text, never an empty string dressed up as
  a successful read;
* **caption** from an injected vision-language port, and *only* from there. No
  VLM means ``caption is None``. Composing "Image IMG_2381.png (JPEG 3024x4032)"
  and storing it in the caption field would make metadata indistinguishable
  from a model's description forever after;
* **embedding** from an injected vision port, which lives in its own vector
  space (see :mod:`latticeai.core.embedding_providers`) and therefore its own
  index — text queries reach images through OCR/caption text, not by scoring a
  BGE vector against CLIP vectors.

Brain Core owns none of those models. Every heavy dependency arrives as an
injected callable (:class:`MultimodalPorts`), which is also why this module
imports nothing from ``latticeai``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .quiet import quiet
from .utils import utc_now_iso

# ── modality taxonomy ────────────────────────────────────────────────────────
MODALITY_TEXT = "text"
MODALITY_IMAGE = "image"
MODALITY_AUDIO = "audio"
MODALITY_VIDEO = "video"

IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic"}
)
# Containers that are *only* ever audio. ``.mp4``/``.webm`` are deliberately
# absent: by extension alone they are video, and a voice memo recorded in one
# of them arrives through an explicit audio MIME type (or through
# ``VoiceCaptureService``, where the user already said "this is a memo").
# ``.mid``/``.midi`` are listed for a second reason: CPython's *built-in* mime
# table has neither, so ``mimetypes`` answers "audio/midi" only on a host that
# ships a system mime file (macOS reads /etc/apache2/mime.types; a slim Linux
# container has nothing). Leaving them to the fallback let the platform decide
# what a MIDI file is — a module table exists precisely so it does not.
AUDIO_EXTENSIONS = frozenset(
    {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".mid", ".midi"}
)
VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"})

#: Why ``video`` is recognized and still refused — surfaced to the caller.
VIDEO_OUT_OF_SCOPE = (
    "video ingestion is out of scope in this release (keyframe extraction is "
    "not implemented); the file was not stored"
)

#: Longest OCR/caption body kept on the node (a screenshot is not a novel).
MAX_INDEX_TEXT_CHARS = 20_000
#: Summary column budget, matching every other ingest door in the graph.
SUMMARY_CHARS = 500
#: Fixed-width chunking for OCR bodies that outgrow the summary.
IMAGE_CHUNK_CHARS = 900
#: Longest edge of the stored thumbnail, in pixels.
THUMBNAIL_EDGE = 96
#: A thumbnail is a UI affordance, not an archive — drop it past this size.
MAX_THUMBNAIL_CHARS = 24_000


def detect_modality(
    path: Optional[str] = None, mime_type: Optional[str] = None
) -> str:
    """``text`` | ``image`` | ``audio`` | ``video`` for one candidate file.

    The declared MIME type wins when it carries a usable top-level type: the
    capture surface saw the bytes, this function only sees a name. Otherwise
    the extension decides, and an unknown extension is ``text`` so existing
    behaviour is untouched.
    """
    declared = str(mime_type or "").strip().lower().split(";")[0].split("/")[0]
    if declared in {MODALITY_IMAGE, MODALITY_AUDIO, MODALITY_VIDEO}:
        return declared
    # The extension tables come before ``mimetypes`` on purpose: they are where
    # this module's decisions live (``.mp4`` is video unless someone who saw
    # the bytes says otherwise), and the stdlib table varies by platform.
    suffix = Path(str(path or "")).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return MODALITY_IMAGE
    if suffix in AUDIO_EXTENSIONS:
        return MODALITY_AUDIO
    if suffix in VIDEO_EXTENSIONS:
        return MODALITY_VIDEO
    if path:
        guessed, _ = mimetypes.guess_type(str(path))
        top = str(guessed or "").split("/")[0]
        if top in {MODALITY_IMAGE, MODALITY_AUDIO, MODALITY_VIDEO}:
            return top
    return MODALITY_TEXT


# ── injected capability ports ────────────────────────────────────────────────
@dataclass
class MultimodalPorts:
    """The optional model-backed capabilities Brain Core cannot ship itself.

    Every field is a plain callable so the app layer can build it from
    ``latticeai.core.embedding_providers`` without Brain Core ever importing
    that package. ``None`` everywhere is the honest default: OCR still runs
    (``pytesseract`` is a local binary, not a model download), and everything
    else reports itself as unavailable.
    """

    #: ``(image_path) -> caption or None`` — a loaded VLM, or nothing.
    captioner: Optional[Callable[[str], Optional[str]]] = None
    #: ``(image_path) -> vector`` in the image space (raises when it cannot).
    vision_embedder: Optional[Callable[[str], List[float]]] = None
    #: ``(audio_path) -> transcript`` (raises/returns empty when it cannot).
    transcriber: Optional[Callable[[str], str]] = None
    #: Identity of the vision model, recorded next to every image vector.
    vision_model_id: str = ""
    #: ``image`` (own index + late fusion) or ``shared`` (same space as text).
    vision_space: str = MODALITY_IMAGE

    def describe(self) -> Dict[str, Any]:
        """What this install can honestly do with a picture or a recording."""
        return {
            "caption": self.captioner is not None,
            "vision_embedding": self.vision_embedder is not None,
            "transcription": self.transcriber is not None,
            "vision_model_id": self.vision_model_id,
            "vision_space": self.vision_space,
        }


# ── image facts ──────────────────────────────────────────────────────────────
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


# ── graph write ──────────────────────────────────────────────────────────────
def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8", "ignore")).hexdigest()


def _split_index_text(text: str) -> List[str]:
    """Fixed-width split for OCR bodies — no markdown or code structure here."""
    body = str(text or "").strip()
    if len(body) <= SUMMARY_CHARS:
        return []
    return [
        body[start : start + IMAGE_CHUNK_CHARS]
        for start in range(0, len(body), IMAGE_CHUNK_CHARS)
    ]


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
    from .graph._kg_common import (  # local: keeps this module's import light
        _classify_node_type,
        _extract_concepts,
    )

    # The id derivation is imported rather than re-derived: two copies of a
    # node-id rule diverge, and a diverged id is a duplicate concept nobody
    # can see.
    from .graph.ingest import _scoped_slug_id

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


# ── audio ────────────────────────────────────────────────────────────────────
@dataclass
class AudioFacts:
    """A recording, its transcript, and how honestly we got one."""

    path: str
    #: ``ok`` | ``unavailable`` | ``failed`` | ``supplied``
    transcription_status: str = "unavailable"
    transcript: str = ""
    detail: str = ""
    segments: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def searchable(self) -> bool:
        return bool(self.transcript)


def transcribe_audio(
    path: str,
    *,
    ports: Optional[MultimodalPorts] = None,
    transcript: Optional[str] = None,
) -> AudioFacts:
    """Transcribe a recording through the injected port, or say why not.

    ``transcript`` lets a caller that already has text (a phone's own
    dictation, ``VoiceCaptureService``) skip the local model entirely. An
    absent transcriber yields ``transcription_status="unavailable"`` and an
    empty transcript — the recording is still remembered by title and path,
    and the result never claims it is searchable.
    """
    ports = ports or MultimodalPorts()
    supplied = str(transcript or "").strip()
    if supplied:
        return AudioFacts(path=str(path), transcription_status="supplied", transcript=supplied)
    if ports.transcriber is None:
        return AudioFacts(
            path=str(path),
            transcription_status="unavailable",
            detail="no local transcriber is configured",
        )
    try:
        text = str(ports.transcriber(str(path)) or "").strip()
    except Exception as exc:  # noqa: BLE001 — a broken transcriber is a state
        return AudioFacts(path=str(path), transcription_status="failed", detail=str(exc))
    if not text:
        return AudioFacts(
            path=str(path),
            transcription_status="failed",
            detail="the transcriber returned no text",
        )
    return AudioFacts(path=str(path), transcription_status="ok", transcript=text)


def audio_quality_score(facts: AudioFacts) -> Dict[str, Any]:
    """How much of this recording is actually retrievable later."""
    if not facts.transcript:
        return {"score": 0.0, "reasons": ["no_transcript"]}
    # A transcript is text: length is the only honest extra signal here, and
    # the text pipeline scores the wording itself downstream.
    score = 0.5 + 0.5 * min(1.0, len(facts.transcript) / 400.0)
    return {"score": round(score, 4), "reasons": ["transcript"]}


__all__ = [
    "AUDIO_EXTENSIONS",
    "IMAGE_CHUNK_CHARS",
    "IMAGE_EXTENSIONS",
    "MAX_INDEX_TEXT_CHARS",
    "MAX_THUMBNAIL_CHARS",
    "MODALITY_AUDIO",
    "MODALITY_IMAGE",
    "MODALITY_TEXT",
    "MODALITY_VIDEO",
    "SUMMARY_CHARS",
    "THUMBNAIL_EDGE",
    "VIDEO_EXTENSIONS",
    "VIDEO_OUT_OF_SCOPE",
    "AudioFacts",
    "ImageFacts",
    "MultimodalPorts",
    "audio_quality_score",
    "detect_modality",
    "extract_image_facts",
    "image_node_id",
    "image_quality_score",
    "transcribe_audio",
    "write_image_memory",
]
