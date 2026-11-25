from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import importlib.util
import logging
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from fastapi import APIRouter, Request

from latticeai.api.agent_worker_seam import SEAM_RATE_BUCKET
from latticeai.api.agent_worker_seam import _seam_open as seam_open
from latticeai.core.embedding_providers.base import EmbeddingIndexContract
from latticeai.core.embedding_providers.profiles import (
    RECOMMENDED_EMBEDDING_MODEL,
    RECOMMENDED_EMBEDDING_PROFILE,
)
from latticeai.core.messages import MESSAGES, http_error, resolve_language
from latticeai.core.quiet import quiet
from latticeai.services.voice_capture import (
    MAX_AUDIO_BYTES,
    SUPPORTED_AUDIO_EXTENSIONS,
)
from latticeai.tools import _CJK_FONT_CANDIDATES, ToolError
from latticeai.tools.documents import read_document

logger = logging.getLogger(__name__)

EMBED_KINDS: Tuple[str, ...] = ("query", "passage")

EXTRACT_KINDS: Tuple[str, ...] = ("message", "document")
EXTRACT_LIMITS: Dict[str, int] = {"message": 12, "document": 15}

PASSAGE_MAX_CHARS = 50_000

EMBED_MAX_BATCH = 256

CJK_FONT_CANDIDATES: List[str] = list(_CJK_FONT_CANDIDATES)

WORKER_COMPUTE_MESSAGES: Dict[str, Dict[str, str]] = {
    "worker_compute.embedder_unavailable": {
        "ko": "임베딩 공급자가 연결되어 있지 않습니다.",
        "en": "No embedding provider is connected to this worker.",
    },
    "worker_compute.kind_invalid": {
        "ko": "'{kind}' 은(는) 임베딩 종류가 아닙니다. {allowed} 중 하나여야 합니다.",
        "en": "'{kind}' is not an embedding kind. Use one of {allowed}.",
    },
    "worker_compute.content_invalid": {
        "ko": "본문을 base64로 읽지 못했습니다: {reason}",
        "en": "The payload is not valid base64: {reason}",
    },
    "worker_compute.parse_failed": {
        "ko": "문서를 읽지 못했습니다: {reason}",
        "en": "The document could not be parsed: {reason}",
    },
    "worker_compute.render_unavailable": {
        "ko": "이 워커는 '{kind}' 문서를 만들 수 없습니다: {reason}",
        "en": "This worker cannot render '{kind}' documents: {reason}",
    },
    "worker_compute.render_failed": {
        "ko": "'{kind}' 문서 생성이 실패했습니다: {reason}",
        "en": "Rendering the '{kind}' document failed: {reason}",
    },
    "worker_compute.audio_unsupported": {
        "ko": "'{suffix}' 은(는) 지원하는 오디오 형식이 아닙니다. {allowed} 만 가능합니다.",
        "en": "'{suffix}' is not a supported audio container. Supported: {allowed}.",
    },
    "worker_compute.audio_too_large": {
        "ko": "오디오가 {size} bytes 입니다. 한도는 {limit} bytes 입니다.",
        "en": "The audio is {size} bytes; the limit is {limit} bytes.",
    },
    "worker_compute.extract_kind_invalid": {
        "ko": "'{kind}' 은(는) 추출 종류가 아닙니다. {allowed} 중 하나여야 합니다.",
        "en": "'{kind}' is not an extraction kind. Use one of {allowed}.",
    },
    "worker_compute.embed_batch_too_large": {
        "ko": "임베딩 배치가 {count}개입니다. 한 번에 {limit}개까지입니다.",
        "en": "The embed batch has {count} texts; the limit is {limit}.",
    },
    "worker_compute.vector_query_invalid": {
        "ko": "벡터 질의는 embedding_model, embedding_dim, vector 가 필요합니다.",
        "en": "A vector query needs embedding_model, embedding_dim, and vector.",
    },
}


def pointer_tools_available() -> bool:
    return importlib.util.find_spec("pyautogui") is not None


def sysinfo_payload_extras() -> Dict[str, Any]:
    return {
        "capabilities": {"pointer_tools": pointer_tools_available()},
        "python_version": "{}.{}.{}".format(*sys.version_info[:3]),
    }


def register_worker_compute_messages() -> None:
    for key, entry in WORKER_COMPUTE_MESSAGES.items():
        MESSAGES.setdefault(key, entry)


register_worker_compute_messages()


@contextlib.contextmanager
def _temp_payload(data: bytes, suffix: str) -> Iterator[str]:
    handle = tempfile.NamedTemporaryFile(
        prefix="ltcai-worker-", suffix=suffix, delete=False
    )
    try:
        handle.write(data)
        handle.close()
        yield handle.name
    finally:
        try:
            Path(handle.name).unlink(missing_ok=True)
        except OSError:
            quiet("worker compute temp cleanup")


def _suffix_for(filename: Optional[str], mime: Optional[str], default: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if not suffix:
        subtype = str(mime or "").split("/")[-1].split(";")[0].strip().lower()
        suffix = f".{subtype}" if subtype else ""
    return suffix or default


def _callable_identity(fn: Any) -> str:
    module = str(getattr(fn, "__module__", "") or "")
    name = str(getattr(fn, "__qualname__", "") or type(fn).__name__)
    return f"{module}.{name}" if module else name


from .worker_compute_render import (
    AsrRequest,
    EmbedRequest,
    ExtractRequest,
    ParseRequest,
    RenderDocxRequest,
    RenderPdfRequest,
    RenderPptxRequest,
    RenderXlsxRequest,
    VectorQueryRequest,
    build_docx_bytes,
    build_pdf_bytes,
    build_pptx_bytes,
    build_xlsx_bytes,
)


def build_extract_reply(text: str, kind: str) -> Dict[str, Any]:
    from lattice_brain.graph._kg_common import (
        _classify_node_type,
        _extract_concepts,
        _extract_triples,
        _semantic_items,
    )

    limit = EXTRACT_LIMITS[kind]
    raw_concepts = _extract_concepts(text, limit=limit)
    concepts = [
        {"text": concept, "node_type": _classify_node_type(concept, text)}
        for concept in raw_concepts
    ]
    triples: List[Dict[str, Any]] = []
    for triple in _extract_triples(text, raw_concepts):
        triples.append(
            {
                "subject": triple["subject"],
                "object": triple["object"],
                "relation": triple["relation"],
                "weight": float(triple.get("weight") or 1.0),
                "context": str(triple.get("context") or ""),
                "evidence": str(triple.get("evidence") or ""),
                "confidence": triple.get("confidence"),
            }
        )
    semantic = [
        {
            "item_type": item["type"],
            "title": item["title"],
            "summary": item["summary"],
            "raw": dict(item),
        }
        for item in _semantic_items(text)
    ]
    return {"concepts": concepts, "triples": triples, "semantic": semantic}


@dataclass(frozen=True)
class _WorkerComputeDeps:
    embedder: Any
    transcriber: Optional[Callable[[str], str]]
    require_user: Callable[[Request], Any]
    enforce_rate_limit: Callable[[str, str], None]
    db_path: Any


def _require_seam(request: Request) -> None:
    if not seam_open():
        raise http_error(404, "agent_seam.disabled", resolve_language(request))


def _admit(deps: _WorkerComputeDeps, request: Request) -> str:
    current_user = deps.require_user(request)
    deps.enforce_rate_limit(current_user, SEAM_RATE_BUCKET)
    return str(current_user or "")


def _decode_b64_payload(payload: str, language: str) -> bytes:
    try:
        return base64.b64decode(str(payload or ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise http_error(
            422, "worker_compute.content_invalid", language, reason=str(exc)
        ) from exc


async def _render_document_payload(
    kind: str,
    builder: Callable[[], bytes],
    language: str,
    **extra: Any,
) -> Dict[str, Any]:
    try:
        payload = await asyncio.to_thread(builder)
    except ToolError as exc:
        raise http_error(
            503,
            "worker_compute.render_unavailable",
            language,
            kind=kind,
            reason=str(exc),
        ) from exc
    except Exception as exc:
        logger.warning("worker render %s failed: %s", kind, exc)
        raise http_error(
            500,
            "worker_compute.render_failed",
            language,
            kind=kind,
            reason=str(exc),
        ) from exc
    return {
        "content_b64": base64.b64encode(payload).decode("ascii"),
        "bytes": len(payload),
        **extra,
    }


def _resolve_embed_contract(embedder: Any, provider: Any) -> Tuple[str, Dict[str, Any]]:
    grade = getattr(provider, "grade", None)
    if grade is None:
        grade = "fallback" if embedder.active == "hash" else "production"
    contract_fn = getattr(provider, "index_contract", None)
    if callable(contract_fn):
        return str(grade), contract_fn().as_dict()
    model_id = str(getattr(provider, "model_id", "") or "")
    is_semantic = (
        grade != "fallback"
        and embedder.active != "hash"
        and not model_id.startswith("lattice-local-hash")
        and not model_id.startswith("lattice-fallback-")
    )
    contract = EmbeddingIndexContract(
        provider=str(embedder.active or "hash"),
        model_id=model_id,
        model_revision=getattr(provider, "model_revision", None),
        dimension=int(getattr(provider, "dim", 0) or 0),
        passage_mode=str(getattr(provider, "passage_mode", "raw") or "raw"),
        query_mode=str(getattr(provider, "query_mode", "raw") or "raw"),
        grade=str(grade),
        semantic=is_semantic,
    ).as_dict()
    return str(grade), contract


def _register_embed_and_parse_routes(
    router: APIRouter, deps: _WorkerComputeDeps
) -> None:
    @router.post("/worker/embed")
    async def worker_embed(req: EmbedRequest, request: Request):
        _require_seam(request)
        _admit(deps, request)
        language = resolve_language(request)
        if deps.embedder is None:
            raise http_error(503, "worker_compute.embedder_unavailable", language)
        kind = req.kind.strip().lower()
        if kind not in EMBED_KINDS:
            raise http_error(
                422,
                "worker_compute.kind_invalid",
                language,
                kind=req.kind,
                allowed=", ".join(EMBED_KINDS),
            )
        provider = deps.embedder.provider
        texts = list(req.texts)
        if len(texts) > EMBED_MAX_BATCH:
            raise http_error(
                422,
                "worker_compute.embed_batch_too_large",
                language,
                count=str(len(texts)),
                limit=str(EMBED_MAX_BATCH),
            )
        if kind == "passage":
            texts = [text[:PASSAGE_MAX_CHARS] for text in texts]
        role_aware = getattr(provider, "embed_batch_for", None)
        vectors = (
            await asyncio.to_thread(role_aware, texts, kind)
            if callable(role_aware)
            else await asyncio.to_thread(provider.embed_batch, texts)
        )
        grade, contract = _resolve_embed_contract(deps.embedder, provider)
        return {
            "vectors": vectors,
            "dim": provider.dim,
            "provider": deps.embedder.active,
            "model_id": provider.model_id,
            "model_revision": contract["model_revision"],
            "normalization": contract["normalization"],
            "passage_mode": contract["passage_mode"],
            "query_mode": contract["query_mode"],
            "index_version": contract["index_version"],
            "kind": kind,
            "grade": grade,
            "semantic": contract["semantic"],
            "contract": contract,
            "recommended_model": RECOMMENDED_EMBEDDING_MODEL,
            "recommended_profile": RECOMMENDED_EMBEDDING_PROFILE,
        }

    @router.post("/worker/parse")
    async def worker_parse(req: ParseRequest, request: Request):
        _require_seam(request)
        _admit(deps, request)
        language = resolve_language(request)
        data = _decode_b64_payload(req.content_b64, language)
        suffix = Path(req.filename or "").suffix.lower()
        with _temp_payload(data, suffix) as tmp_path:
            try:
                parsed = await asyncio.to_thread(read_document, tmp_path)
            except ToolError as exc:
                raise http_error(
                    400, "worker_compute.parse_failed", language, reason=str(exc)
                ) from exc
        parsed.pop("path", None)
        parsed["filename"] = req.filename
        return parsed


def _register_render_routes(router: APIRouter, deps: _WorkerComputeDeps) -> None:
    @router.post("/worker/render/docx")
    async def worker_render_docx(req: RenderDocxRequest, request: Request):
        _require_seam(request)
        _admit(deps, request)
        return await _render_document_payload(
            "docx",
            lambda: build_docx_bytes(req.title, req.body),
            resolve_language(request),
        )

    @router.post("/worker/render/xlsx")
    async def worker_render_xlsx(req: RenderXlsxRequest, request: Request):
        _require_seam(request)
        _admit(deps, request)
        return await _render_document_payload(
            "xlsx",
            lambda: build_xlsx_bytes(req.rows, req.sheet_name),
            resolve_language(request),
            rows=len(req.rows),
        )

    @router.post("/worker/render/pptx")
    async def worker_render_pptx(req: RenderPptxRequest, request: Request):
        _require_seam(request)
        _admit(deps, request)
        return await _render_document_payload(
            "pptx",
            lambda: build_pptx_bytes(req.title, req.slides),
            resolve_language(request),
            slides=len(req.slides) + 1,
        )

    @router.post("/worker/render/pdf")
    async def worker_render_pdf(req: RenderPdfRequest, request: Request):
        _require_seam(request)
        _admit(deps, request)
        return await _render_document_payload(
            "pdf",
            lambda: build_pdf_bytes(req.title, req.body),
            resolve_language(request),
        )


def _register_media_and_extract_routes(
    router: APIRouter, deps: _WorkerComputeDeps
) -> None:
    @router.post("/worker/asr")
    async def worker_asr(req: AsrRequest, request: Request):
        _require_seam(request)
        _admit(deps, request)
        language = resolve_language(request)
        data = _decode_b64_payload(req.audio_b64, language)
        suffix = _suffix_for(req.filename, req.mime, ".m4a")
        if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
            raise http_error(
                400,
                "worker_compute.audio_unsupported",
                language,
                suffix=suffix,
                allowed=", ".join(sorted(SUPPORTED_AUDIO_EXTENSIONS)),
            )
        if len(data) > MAX_AUDIO_BYTES:
            raise http_error(
                413,
                "worker_compute.audio_too_large",
                language,
                size=len(data),
                limit=MAX_AUDIO_BYTES,
            )
        if deps.transcriber is None:
            return {
                "text": "",
                "segments": None,
                "provider": "",
                "status": "unavailable",
                "detail": "no local transcriber is configured",
            }
        provider = _callable_identity(deps.transcriber)
        with _temp_payload(data, suffix) as tmp_path:
            try:
                raw = await asyncio.to_thread(deps.transcriber, tmp_path)
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                logger.warning("worker asr failed: %s", exc)
                return {
                    "text": "",
                    "segments": None,
                    "provider": provider,
                    "status": "failed",
                    "detail": str(exc),
                }
        text = str(raw or "").strip()
        if not text:
            return {
                "text": "",
                "segments": None,
                "provider": provider,
                "status": "unavailable",
                "detail": "the transcriber returned no text",
            }
        return {
            "text": text,
            "segments": None,
            "provider": provider,
            "status": "ok",
            "detail": "",
        }

    @router.post("/worker/extract")
    async def worker_extract(req: ExtractRequest, request: Request):
        _require_seam(request)
        _admit(deps, request)
        language = resolve_language(request)
        kind = req.kind.strip().lower()
        if kind not in EXTRACT_KINDS:
            raise http_error(
                422,
                "worker_compute.extract_kind_invalid",
                language,
                kind=req.kind,
                allowed=", ".join(EXTRACT_KINDS),
            )
        return await asyncio.to_thread(build_extract_reply, req.text, kind)

    @router.post("/worker/vector/query")
    async def worker_vector_query(req: VectorQueryRequest, request: Request):
        _require_seam(request)
        _admit(deps, request)
        language = resolve_language(request)
        if not req.embedding_model or req.embedding_dim <= 0 or not req.vector:
            raise http_error(422, "worker_compute.vector_query_invalid", language)
        from latticeai.core.vector_index import query_sidecar

        return await asyncio.to_thread(
            query_sidecar,
            workspace=req.workspace,
            embedding_model=req.embedding_model,
            embedding_dim=req.embedding_dim,
            vector=req.vector,
            k=req.k,
            db_path=deps.db_path,
        )


def create_worker_compute_router(
    *,
    embedder: Any,
    transcriber: Optional[Callable[[str], str]] = None,
    require_user: Callable[[Request], Any],
    enforce_rate_limit: Callable[[str, str], None],
    db_path: Any = None,
) -> APIRouter:
    deps = _WorkerComputeDeps(
        embedder=embedder,
        transcriber=transcriber,
        require_user=require_user,
        enforce_rate_limit=enforce_rate_limit,
        db_path=db_path,
    )
    router = APIRouter()
    _register_embed_and_parse_routes(router, deps)
    _register_render_routes(router, deps)
    _register_media_and_extract_routes(router, deps)
    return router


__all__ = [
    "CJK_FONT_CANDIDATES",
    "EMBED_KINDS",
    "EMBED_MAX_BATCH",
    "EXTRACT_KINDS",
    "EXTRACT_LIMITS",
    "PASSAGE_MAX_CHARS",
    "WORKER_COMPUTE_MESSAGES",
    "AsrRequest",
    "EmbedRequest",
    "ExtractRequest",
    "ParseRequest",
    "VectorQueryRequest",
    "RenderDocxRequest",
    "RenderPdfRequest",
    "RenderPptxRequest",
    "RenderXlsxRequest",
    "build_docx_bytes",
    "build_extract_reply",
    "build_pdf_bytes",
    "build_pptx_bytes",
    "build_xlsx_bytes",
    "create_worker_compute_router",
    "pointer_tools_available",
    "register_worker_compute_messages",
    "sysinfo_payload_extras",
]
