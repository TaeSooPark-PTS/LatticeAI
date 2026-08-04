"""Brain Review Queue API router (5.6.0).

The suggestion inbox the /app Review view drives. Follows the existing
auth/workspace dependency pattern (``require_user`` + ``gate_read``/``gate_write``)
and exposes explicit response models so the OpenAPI types are usable from the
frontend without massaging.

Action semantics live in :class:`~latticeai.services.review_queue.ReviewQueueService`:

* ``approve`` / ``dismiss`` / ``snooze`` / ``unsnooze`` are status transitions;
  an illegal transition returns **409**.
* ``run_now`` previews/regenerates without changing status (back-links the run).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from latticeai.core.messages import http_error, resolve_language, translate
from latticeai.services.change_proposals import ProposalConflictError
from latticeai.services.review_queue import InvalidReviewTransition, ReviewQueueService


class ReviewItem(BaseModel):
    id: str
    status: str
    effective_status: str
    title: str
    summary: str = ""
    source: str = "workflow_run"
    kind: str = "suggestion"
    payload: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    snoozed_until: Optional[str] = None
    user_email: Optional[str] = None
    workspace_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ReviewItemList(BaseModel):
    items: List[ReviewItem] = Field(default_factory=list)


class CreateReviewItemRequest(BaseModel):
    title: str
    summary: str = ""
    source: str = "workflow_run"
    kind: str = "suggestion"
    payload: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)


class SnoozeRequest(BaseModel):
    until: str


class DismissRequest(BaseModel):
    """Optional dismissal context (e.g. why a change proposal was rejected)."""

    reason: str = ""


class ReviewCounts(BaseModel):
    pending: int = 0
    snoozed: int = 0
    pending_by_source: Dict[str, int] = Field(default_factory=dict)


def create_review_queue_router(
    *,
    service: ReviewQueueService,
    require_user: Callable[[Request], str],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
    run_review_item: Callable[..., Any],
    append_audit_event: Callable[..., None],
    change_proposals: Any = None,
) -> APIRouter:
    """``change_proposals`` (optional) closes the governance loop: approving a
    ``change_proposal`` item from the Review Center applies the staged content
    via :class:`~latticeai.services.change_proposals.ChangeProposalService`
    instead of merely flipping the status — the same single application path
    the /api/proposals surface uses."""
    router = APIRouter()

    @router.get("/automation/reviews", response_model=ReviewItemList)
    async def list_items(
        request: Request, status: Optional[str] = None, source: Optional[str] = None,
    ):
        user = require_user(request)
        scope = gate_read(request)
        return service.list(workspace_id=scope, user_email=user, status=status, source=source)

    @router.post("/automation/reviews", response_model=ReviewItem)
    async def create_item(req: CreateReviewItemRequest, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        try:
            item = service.create(
                title=req.title,
                summary=req.summary,
                source=req.source,
                kind=req.kind,
                payload=req.payload,
                provenance=req.provenance,
                user_email=user,
                workspace_id=scope,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        append_audit_event("review_item_created", user_email=user, item_id=item["id"])
        return item

    # NOTE: declared before /automation/reviews/{item_id} so "counts" never
    # resolves as an item id.
    @router.get("/automation/reviews/counts", response_model=ReviewCounts)
    async def review_counts(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.counts(workspace_id=scope, user_email=user)

    @router.get("/automation/reviews/{item_id}", response_model=ReviewItem)
    async def get_item(item_id: str, request: Request):
        require_user(request)
        scope = gate_read(request)
        try:
            return service.get(item_id, workspace_id=scope)
        except FileNotFoundError as exc:
            raise http_error(404, "review.item_not_found", resolve_language(request)) from exc

    @router.post("/automation/reviews/{item_id}/approve", response_model=ReviewItem)
    async def approve_item(item_id: str, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        # change_proposal items must apply the staged content on approve —
        # otherwise the item flips to "approved" while nothing hits disk.
        if change_proposals is not None:
            try:
                stored = service.get(item_id, workspace_id=scope)
            except FileNotFoundError as exc:
                raise http_error(404, "review.item_not_found", resolve_language(request)) from exc
            if stored.get("source") == "change_proposal":
                if stored.get("effective_status") not in ("pending", "snoozed"):
                    raise HTTPException(
                        status_code=409,
                        detail=translate(
                    "review.cannot_approve_in_status",
                    resolve_language(request),
                    status=stored.get("status"),
                ),
                    )
                try:
                    applied = change_proposals.approve_and_apply(
                        item_id, user_email=user, workspace_id=scope
                    )
                except ProposalConflictError as exc:
                    # The file drifted since staging (or the proposal was
                    # already resolved): same 409-on-replay semantics as the
                    # other illegal transitions, plus a rebase hint so the UI
                    # can offer "re-stage against the current content".
                    raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
                except (KeyError, FileNotFoundError) as exc:
                    raise HTTPException(status_code=404, detail=str(exc)) from exc
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                append_audit_event("review_item_approve", user_email=user, item_id=item_id)
                return applied["item"]
        return _act(request, item_id, "approve")

    @router.post("/automation/reviews/{item_id}/dismiss", response_model=ReviewItem)
    async def dismiss_item(
        item_id: str, request: Request, req: Optional[DismissRequest] = None
    ):
        user = require_user(request)
        scope = gate_write(request)
        try:
            item = service.dismiss(
                item_id, workspace_id=scope, reason=(req.reason if req else None)
            )
        except FileNotFoundError as exc:
            raise http_error(404, "review.item_not_found", resolve_language(request)) from exc
        except InvalidReviewTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        append_audit_event("review_item_dismiss", user_email=user, item_id=item_id)
        return item

    @router.post("/automation/reviews/{item_id}/snooze", response_model=ReviewItem)
    async def snooze_item(item_id: str, req: SnoozeRequest, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        try:
            item = service.snooze(item_id, until=req.until, workspace_id=scope)
        except FileNotFoundError as exc:
            raise http_error(404, "review.item_not_found", resolve_language(request)) from exc
        except InvalidReviewTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        append_audit_event("review_item_snooze", user_email=user, item_id=item_id)
        return item

    @router.post("/automation/reviews/{item_id}/unsnooze", response_model=ReviewItem)
    async def unsnooze_item(item_id: str, request: Request):
        return _act(request, item_id, "unsnooze")

    @router.post("/automation/reviews/{item_id}/run_now", response_model=ReviewItem)
    async def run_now_item(item_id: str, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        try:
            item = service.run_now(
                item_id,
                runner=lambda stored: run_review_item(stored, user_email=user, scope=scope),
                workspace_id=scope,
            )
        except FileNotFoundError as exc:
            raise http_error(404, "review.item_not_found", resolve_language(request)) from exc
        except InvalidReviewTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        append_audit_event("review_item_run_now", user_email=user, item_id=item_id)
        return item

    def _act(request: Request, item_id: str, action: str) -> Dict[str, Any]:
        user = require_user(request)
        scope = gate_write(request)
        fn = getattr(service, action)
        try:
            item = fn(item_id, workspace_id=scope)
        except FileNotFoundError as exc:
            raise http_error(404, "review.item_not_found", resolve_language(request)) from exc
        except InvalidReviewTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        append_audit_event(f"review_item_{action}", user_email=user, item_id=item_id)
        return item

    return router
