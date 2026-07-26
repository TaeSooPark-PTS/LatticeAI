"""Evidence → action router (v9.9.6).

``POST /api/evidence/actions`` turns an answer's citations into ready-to-send,
evidence-scoped follow-up prompts. Read-only and deterministic: it resolves
graph nodes and composes text — no model call, no writes. Execution stays on
the existing chat / file-generation path so there is exactly one road from a
prompt to an artifact.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field


class EvidenceActionsRequest(BaseModel):
    """Citations from one answer (the ids the grounding badge reported)."""

    question: str = ""
    source_ids: List[str] = Field(default_factory=list)
    language: str = "ko"


def create_evidence_actions_router(
    *,
    service: Any,
    require_user: Callable[[Request], Any],
    allowed_workspaces_for: Optional[Callable[[Any], Any]] = None,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/evidence/actions")
    async def evidence_actions(req: EvidenceActionsRequest, request: Request):
        user = require_user(request)
        scope = allowed_workspaces_for(user) if allowed_workspaces_for is not None else None
        return service.actions_for(
            question=req.question,
            source_ids=req.source_ids,
            language=req.language,
            allowed_workspaces=scope,
        )

    return router


__all__ = ["create_evidence_actions_router", "EvidenceActionsRequest"]
