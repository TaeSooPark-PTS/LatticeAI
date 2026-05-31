"""Document upload parsing, safety checks, and knowledge-graph ingestion."""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile

from tools import ToolError, read_document


async def process_uploaded_document(
    *,
    request: Request,
    file: UploadFile,
    current_user: str,
    enable_graph: bool,
    knowledge_graph,
    bytes_match_extension,
    classify_sensitive_message,
    append_audit_event,
    enforce_rate_limit,
) -> dict:
    enforce_rate_limit(current_user, "upload")
    suffix = Path(file.filename or "upload").suffix.lower()
    allowed = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv"}
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 형식: {suffix}")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="파일이 너무 큽니다. 최대 10MB.")
    if not bytes_match_extension(contents, suffix):
        raise HTTPException(status_code=400, detail=f"파일 내용이 확장자({suffix})와 일치하지 않습니다.")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        result = read_document(tmp_path)
        sensitive = classify_sensitive_message(
            {
                "role": "document",
                "content": result.get("content") or result.get("preview") or "",
                "user_email": current_user,
                "timestamp": datetime.now().isoformat(),
            },
            -1,
        )
        try:
            if not (enable_graph and knowledge_graph):
                raise RuntimeError("graph disabled")
            graph_result = knowledge_graph.ingest_document(
                Path(tmp_path),
                original_filename=file.filename,
                mime_type=file.content_type,
                uploader=current_user,
                conversation_id=request.query_params.get("conversation_id"),
                extracted=result,
            )
            result["knowledge_graph"] = {
                "node_id": graph_result["node_id"],
                "sha256": graph_result["sha256"],
            }
        except Exception as graph_error:
            logging.warning("knowledge graph document ingest failed: %s", graph_error)
            result["knowledge_graph"] = {"error": str(graph_error)}

        append_audit_event(
            "document_upload",
            user_email=current_user,
            conversation_id=request.query_params.get("conversation_id"),
            filename=file.filename,
            mime_type=file.content_type,
            ext=suffix,
            bytes=len(contents),
            extracted_chars=result.get("chars"),
            graph_node=(result.get("knowledge_graph") or {}).get("node_id"),
            content_preview=sensitive.get("preview"),
            sensitivity=sensitive.get("sensitivity"),
            sensitive_labels=sensitive.get("labels") or [],
        )
    except ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass

    result["original_filename"] = file.filename
    return result


__all__ = ["process_uploaded_document"]
