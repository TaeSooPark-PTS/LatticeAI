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


class BulkActionRequest(BaseModel):
    """Decide several review items at once, named explicitly.

    ``ids`` is required and never defaults to "everything pending": a bulk
    action whose scope is implicit is how an inbox gets emptied by accident.
    """

    ids: List[str] = Field(default_factory=list)
    reason: str = ""


class BulkActionOutcome(BaseModel):
    """Per-item verdict, so a partial success is legible rather than a total."""

    id: str
    status: str  # ok | not_found | conflict | failed
    item_status: Optional[str] = None
    detail: Optional[str] = None


class BulkActionResult(BaseModel):
    action: str
    requested: int
    succeeded: int
    failed: int
    results: List[BulkActionOutcome] = Field(default_factory=list)


#: Ceiling on one bulk call. Large enough for "clear today's inbox", small
#: enough that one request cannot walk the whole table inside a web worker.
BULK_ACTION_CAP = 200


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

    # NOTE: declared *before* /automation/reviews/{item_id} for the same reason
    # "counts" is — routes match in declaration order, so a later /bulk/... would
    # be swallowed by the {item_id} matcher and answer 404 for a missing item.
    @router.post("/automation/reviews/bulk/approve", response_model=BulkActionResult)
    async def bulk_approve(req: BulkActionRequest, request: Request):
        """Approve several items, reporting each one's outcome individually."""
        return _bulk(request, req, "approve")

    @router.post("/automation/reviews/bulk/dismiss", response_model=BulkActionResult)
    async def bulk_dismiss(req: BulkActionRequest, request: Request):
        """Dismiss several items, reporting each one's outcome individually."""
        return _bulk(request, req, "dismiss")

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

    def _bulk(request: Request, req: BulkActionRequest, action: str) -> Dict[str, Any]:
        """One transition applied N times through the *same* single-item guard.

        Deliberately not a bulk UPDATE. Every id goes through the ordinary
        service call, so an already-approved item still 409s, a
        ``change_proposal`` still applies its staged content, and the audit
        trail records N decisions — because that is what happened. A failure
        on one id never aborts the rest, and the response says exactly which
        ones did not land instead of returning a single number the caller has
        to interpret.
        """
        user = require_user(request)
        scope = gate_write(request)
        language = resolve_language(request)
        wanted = [str(item).strip() for item in req.ids if str(item).strip()]
        if not wanted:
            raise http_error(422, "review.bulk_ids_required", language)
        if len(wanted) > BULK_ACTION_CAP:
            raise http_error(422, "review.bulk_too_many", language, cap=BULK_ACTION_CAP)
        results: List[Dict[str, Any]] = []
        succeeded = 0
        for item_id in wanted:
            outcome = _bulk_one(request, item_id, action, user=user, scope=scope, reason=req.reason)
            if outcome["status"] == "ok":
                succeeded += 1
            results.append(outcome)
        return {
            "action": action,
            "requested": len(wanted),
            "succeeded": succeeded,
            "failed": len(wanted) - succeeded,
            "results": results,
        }

    def _bulk_one(
        request: Request,
        item_id: str,
        action: str,
        *,
        user: str,
        scope: Optional[str],
        reason: str,
    ) -> Dict[str, Any]:
        try:
            if action == "dismiss":
                item = service.dismiss(item_id, workspace_id=scope, reason=reason or None)
            else:
                item = _approve_one(request, item_id, user=user, scope=scope)
        except (FileNotFoundError, KeyError):
            return {"id": item_id, "status": "not_found", "detail": None}
        except InvalidReviewTransition as exc:
            return {"id": item_id, "status": "conflict", "detail": str(exc)}
        except ProposalConflictError as exc:
            return {"id": item_id, "status": "conflict", "detail": str(exc)}
        except HTTPException as exc:
            return {
                "id": item_id,
                "status": "conflict" if exc.status_code == 409 else "failed",
                "detail": str(exc.detail),
            }
        except ValueError as exc:
            return {"id": item_id, "status": "failed", "detail": str(exc)}
        append_audit_event(f"review_item_{action}", user_email=user, item_id=item_id)
        return {"id": item_id, "status": "ok", "item_status": item.get("status"), "detail": None}

    def _approve_one(
        request: Request, item_id: str, *, user: str, scope: Optional[str]
    ) -> Dict[str, Any]:
        """Approve exactly the way the single-item route does, staged content included."""
        if change_proposals is not None:
            stored = service.get(item_id, workspace_id=scope)
            if stored.get("source") == "change_proposal":
                if stored.get("effective_status") not in ("pending", "snoozed"):
                    raise InvalidReviewTransition("approve", str(stored.get("status")))
                applied = change_proposals.approve_and_apply(
                    item_id, user_email=user, workspace_id=scope
                )
                return dict(applied["item"])
        return service.approve(item_id, workspace_id=scope)

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
