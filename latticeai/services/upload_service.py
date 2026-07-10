"""Document upload parsing, safety checks, and knowledge-graph ingestion."""

from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request, UploadFile

from lattice_brain.ingestion import IngestionItem
from tools import ToolError, read_document


def _workspace_scope_from_request(request: Request) -> Optional[str]:
    header = request.headers.get("X-Workspace-Id")
    if header and header.strip():
        return header.strip()
    query = request.query_params.get("workspace_id")
    return query.strip() if query and query.strip() else None


async def process_uploaded_document(
    *,
    request: Request,
    file: UploadFile,
    current_user: str,
    enable_graph: bool,
    knowledge_graph,
    ingestion_pipeline=None,
    bytes_match_extension,
    classify_sensitive_message,
    append_audit_event,
    enforce_rate_limit,
    hooks=None,
    workspace_service=None,
) -> dict:
    enforce_rate_limit(current_user, "upload")
    requested_workspace = _workspace_scope_from_request(request)
    if workspace_service is not None:
        try:
            workspace_id = workspace_service.resolve_write_scope(
                requested_workspace,
                current_user or None,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    else:
        workspace_id = requested_workspace
    suffix = Path(file.filename or "upload").suffix.lower()
    allowed = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv"}
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 형식: {suffix}")

    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="파일이 너무 큽니다. 최대 10MB.")
    if not bytes_match_extension(contents, suffix):
        raise HTTPException(status_code=400, detail=f"파일 내용이 확장자({suffix})와 일치하지 않습니다.")

    # ── pre_upload hook ── may gate the upload before any work happens.
    if hooks is not None:
        pre_up = hooks.fire_hook(
            "pre_upload", "document.upload",
            payload={"filename": file.filename, "ext": suffix, "bytes": len(contents)},
            user_email=current_user,
        )
        if pre_up.get("blocked"):
            raise HTTPException(status_code=403, detail=pre_up.get("block_reason") or "Upload blocked by a pre_upload hook.")

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
        # ── pre_index / post_index hooks bracket the KG ingest (chunk → embed →
        # graph-build), the actual indexing step.
        if hooks is not None:
            hooks.fire_hook("pre_index", "document.index",
                            payload={"filename": file.filename, "chars": result.get("chars")},
                            user_email=current_user)
        try:
            if not (enable_graph and knowledge_graph):
                raise RuntimeError("graph disabled")
            if ingestion_pipeline is not None:
                # v4: uploads enter the brain through the unified ingestion
                # pipeline (provenance + kg_ingest hook lifecycle).
                ingest = ingestion_pipeline.ingest(
                    IngestionItem(
                        source_type="upload",
                        title=file.filename,
                        path=tmp_path,
                        mime_type=file.content_type,
                        owner=current_user,
                        workspace_id=workspace_id,
                        conversation_id=request.query_params.get("conversation_id"),
                        metadata={"extracted": result},
                    ),
                    user_email=current_user,
                )
                if ingest.status != "ok":
                    raise RuntimeError(ingest.detail or f"ingestion {ingest.status}")
                result["knowledge_graph"] = {
                    "node_id": ingest.node_id,
                    "sha256": ingest.content_hash,
                    "provenance_id": ingest.provenance_id,
                }
            else:
                graph_result = knowledge_graph.ingest_document(
                    Path(tmp_path),
                    original_filename=file.filename,
                    mime_type=file.content_type,
                    uploader=current_user,
                    workspace_id=workspace_id,
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
        if hooks is not None:
            _kg = result.get("knowledge_graph") or {}
            hooks.fire_hook("post_index", "document.index",
                            payload={"filename": file.filename, "graph_node": _kg.get("node_id"),
                                     "indexed": bool(_kg.get("node_id")), "error": _kg.get("error")},
                            user_email=current_user)

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

    # ── post_upload hook ── the whole upload → parse → index pipeline finished.
    if hooks is not None:
        kg = result.get("knowledge_graph") or {}
        hooks.fire_hook(
            "post_upload", "document.uploaded",
            payload={
                "filename": file.filename,
                "chars": result.get("chars"),
                "graph_node": kg.get("node_id"),
                "indexed": bool(kg.get("node_id")),
            },
            user_email=current_user,
        )

    result["original_filename"] = file.filename
    return result


__all__ = ["process_uploaded_document"]
