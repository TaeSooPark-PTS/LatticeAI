"""Change proposal API router (v9.6.0).

Read/approve/reject surface over
:class:`~latticeai.services.change_proposals.ChangeProposalService`. The
items live in the shared review queue (source ``change_proposal``), so the
Act review center shows them too; this router adds the proposal-specific
semantics: **approve applies the staged content exactly as reviewed**,
reject discards it, and nothing touches disk while a proposal is pending.
"""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request

from latticeai.services.change_proposals import ChangeProposalService


def create_change_proposals_router(
    *,
    service: ChangeProposalService,
    require_user: Callable[[Request], str],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/proposals")
    async def list_proposals(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.pending(user_email=user, workspace_id=scope)

    @router.post("/api/proposals/{item_id}/approve")
    async def approve_proposal(item_id: str, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        try:
            return service.approve_and_apply(
                item_id, user_email=user, workspace_id=scope
            )
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/proposals/{item_id}/reject")
    async def reject_proposal(item_id: str, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        try:
            return service.reject(item_id, user_email=user, workspace_id=scope)
        except (KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    return router


__all__ = ["create_change_proposals_router"]
