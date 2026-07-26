"""Screenshot ingestion and Knowledge-Graph-backed document generation."""

from __future__ import annotations

import base64
import io
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse, StreamingResponse
from PIL import Image

from latticeai.core.context_builder import (
    format_sources_footnote,
    retrieve_context_for_generation,
)
from latticeai.core.document_generator import DocumentGenerationSession, detect_document_intent


def extract_screenshot_context(image_data: Optional[str]) -> str:
    if not image_data:
        return ""

    lines = ["[SCREENSHOT INGESTION]"]
    try:
        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        lines.append(f"- image_size: {image.width}x{image.height}")
        lines.append(f"- image_mode: {image.mode}")
    except Exception as exc:
        lines.append(f"- image_decode_error: {exc}")
        return "\n".join(lines)

    tesseract_path = shutil.which("tesseract")
    if not tesseract_path:
        lines.append("- ocr: unavailable; install `tesseract` to enable OCR text extraction.")
        return "\n".join(lines)

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="ltcai-screenshot-",
            suffix=".png",
            delete=False,
        ) as temp:
            temp.write(image_bytes)
            temp_path = temp.name

        ocr_text = ""
        for language in ("kor+eng", "eng"):
            completed = subprocess.run(
                [tesseract_path, temp_path, "stdout", "-l", language, "--psm", "6"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                ocr_text = completed.stdout.strip()
                lines.append(f"- ocr_language: {language}")
                break

        if ocr_text:
            lines.extend(("- ocr_text:", ocr_text[:4000]))
        else:
            lines.append("- ocr: no text extracted.")
    except Exception as exc:
        lines.append(f"- ocr_error: {exc}")
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink()
            except OSError:
                pass
    return "\n".join(lines)


@dataclass(frozen=True)
class DocumentPreparation:
    is_document: bool
    context: str
    retrieval: Optional[Dict[str, Any]]


class DocumentGenerationCoordinator:
    """Own per-conversation document sessions and generation finalization."""

    def __init__(
        self,
        *,
        model_router: Any,
        knowledge_graph: Any,
        enable_graph: bool,
        chat_service: Any,
        notify: Any,
    ) -> None:
        self._router = model_router
        self._graph = knowledge_graph
        self._enable_graph = bool(enable_graph)
        self._chat_service = chat_service
        self._notify = notify
        self._sessions: Dict[tuple[str, str, str], DocumentGenerationSession] = {}

    def prepare(
        self,
        req: Any,
        context: str,
        *,
        workspace_id: Optional[str],
    ) -> DocumentPreparation:
        is_document = detect_document_intent(req.message)
        retrieval = None
        if self._enable_graph and self._graph and is_document:
            try:
                retrieval = retrieve_context_for_generation(
                    self._graph,
                    req.message,
                    max_results=10,
                    max_hops=2,
                    allowed_workspaces={workspace_id} if workspace_id else None,
                )
                graph_context = retrieval.get("context_markdown", "")
                if graph_context:
                    context += (
                        "\n\n[KNOWLEDGE GRAPH — Document Generation Context]\n"
                        + graph_context
                    )
                    logging.debug(
                        "Document generation context retrieved from knowledge graph."
                    )
                # Shared context contract (v9.9.6): document generation now
                # reports the same context_quality signal chat does, so a
                # thin-context document is as visible as a thin-context answer.
                if retrieval.get("stats", {}).get("budget_trimmed"):
                    logging.debug("Document context trimmed to the shared budget.")
            except Exception as exc:
                logging.warning("Knowledge graph reinforcement skipped: %s", exc)
        return DocumentPreparation(is_document, context, retrieval)

    async def response(
        self,
        req: Any,
        preparation: DocumentPreparation,
        *,
        model_id: str,
        effective_email: Optional[str],
        workspace_id: Optional[str],
        history_meta: Dict[str, Any],
        trace_seed: Dict[str, Any],
    ):
        if not (
            preparation.is_document
            and self._enable_graph
            and self._graph
        ):
            return None

        key = (
            effective_email or "",
            workspace_id or "",
            req.conversation_id or "default",
        )
        session = self._sessions.setdefault(key, DocumentGenerationSession())
        graph_markdown = (preparation.retrieval or {}).get("context_markdown", "")
        system_prompt = session.get_system_prompt(graph_markdown)
        footnote = format_sources_footnote((preparation.retrieval or {}).get("sources", []))
        # Shared context contract (v9.9.6): the document path records the same
        # context_quality + assembly trace on the answer trace that chat does,
        # so both surfaces answer "how well was this grounded?" identically.
        context_quality = (preparation.retrieval or {}).get("context_quality")
        if isinstance(trace_seed, dict):
            if context_quality:
                trace_seed["context_quality"] = context_quality
            assembly_trace = (preparation.retrieval or {}).get("trace")
            if assembly_trace:
                trace_seed["context_assembly"] = assembly_trace

        if req.stream:
            async def stream_document():
                collected = []
                stream_error = None
                try:
                    async for chunk in self._router.stream_generate_document_as(
                        model_id,
                        req.message,
                        system_prompt,
                        max_tokens=req.max_tokens or 8192,
                        temperature=req.temperature or 0.3,
                    ):
                        collected.append(chunk)
                        yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
                except Exception as exc:
                    stream_error = str(exc)
                    logging.warning("document stream failed: %s", exc)
                    yield f"data: {json.dumps({'error': stream_error}, ensure_ascii=False)}\n\n"

                full_text = "".join(collected)
                if footnote:
                    yield f"data: {json.dumps({'text': footnote}, ensure_ascii=False)}\n\n"
                    full_text += footnote
                if stream_error:
                    full_text = (
                        f"{full_text}\n\n[stream_error] {stream_error}"
                        if full_text
                        else f"[stream_error] {stream_error}"
                    )
                session.update(graph_markdown, full_text, req.conversation_id)
                trace_record = await self._chat_service.persist_answer(
                    question=req.message,
                    response=full_text,
                    conversation_id=req.conversation_id,
                    user_email=effective_email,
                    user_nickname=req.user_nickname,
                    source=req.source,
                    trace=trace_seed,
                    workspace_id=workspace_id,
                    history_meta=history_meta,
                    notify=self._notify,
                )
                yield f"data: {json.dumps({'text': '', 'trace_id': trace_record['id'], 'trace': trace_record}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                stream_document(),
                media_type="text/event-stream",
                headers={"X-Model": model_id, "X-Doc-Gen": "true"},
            )

        result = await self._router.generate_document_as(
            model_id,
            req.message,
            system_prompt,
            max_tokens=req.max_tokens or 8192,
            temperature=req.temperature or 0.3,
        )
        if footnote:
            result += footnote
        response_text = str(result)
        session.update(graph_markdown, response_text, req.conversation_id)
        trace_record = await self._chat_service.persist_answer(
            question=req.message,
            response=response_text,
            conversation_id=req.conversation_id,
            user_email=effective_email,
            user_nickname=req.user_nickname,
            source=req.source,
            trace=trace_seed,
            workspace_id=workspace_id,
            history_meta=history_meta,
            notify=self._notify,
        )
        return JSONResponse(
            content={
                "response": response_text,
                "trace_id": trace_record["id"],
                "trace": trace_record,
                **({"context_quality": context_quality} if context_quality else {}),
            }
        )


__all__ = [
    "DocumentGenerationCoordinator",
    "DocumentPreparation",
    "extract_screenshot_context",
]
