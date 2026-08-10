"""Voice memo capture router (v9.9.7).

``GET  /api/capture/voice/status`` — what this install can actually do with a
voice memo (capture always; transcription only when a local transcriber
exists).

``POST /api/capture/voice`` — ingest one memo (multipart upload) through the
unified ingestion pipeline. Local-only: audio is written to the workspace data
dir and transcribed on this machine or not at all.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from latticeai.core.quiet import quiet

LOGGER = logging.getLogger(__name__)


def create_voice_capture_router(
    *,
    service: Any,
    require_user: Callable[[Request], Any],
    gate_write: Optional[Callable[[Request], Optional[str]]] = None,
    append_audit_event: Optional[Callable[..., None]] = None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/capture/voice/status")
    async def voice_status(request: Request):
        require_user(request)
        return service.status()

    @router.post("/api/capture/voice")
    async def voice_capture(
        request: Request,
        file: UploadFile = File(...),
        title: str = Form(""),
        transcript: str = Form(""),
        conversation_id: str = Form(""),
    ):
        user = require_user(request)
        scope = gate_write(request) if gate_write is not None else None
        suffix = Path(file.filename or "memo.m4a").suffix or ".m4a"
        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix="ltcai-voice-", suffix=suffix, delete=False
            ) as handle:
                shutil.copyfileobj(file.file, handle)
                tmp_path = Path(handle.name)
            result = service.capture(
                str(tmp_path),
                title=title or Path(file.filename or "").stem,
                user_email=user,
                workspace_id=scope,
                conversation_id=conversation_id or None,
                transcript=transcript or None,
            )
        except Exception as exc:  # noqa: BLE001 — surface a clean error, not a 500 trace
            LOGGER.exception("voice capture failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            # tmp_path is None only when the upload failed before the temp file
            # existed — and that path is always re-raising, so the false side of
            # this guard can never reach the statement after the try block.
            if tmp_path is not None:  # pragma: no branch — see comment above
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    quiet()
        if append_audit_event is not None:
            try:
                append_audit_event(
                    "voice_capture",
                    user_email=user,
                    status=result.get("status"),
                    transcription=result.get("transcription"),
                )
            except Exception:  # noqa: BLE001 — audit is advisory
                LOGGER.debug("voice capture audit failed", exc_info=True)
        return result

    return router


__all__ = ["create_voice_capture_router"]
