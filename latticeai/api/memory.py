"""Memory platform + Memory Manager API router (v3.2.0).

Exposes :class:`~latticeai.services.memory_service.MemoryService` so the /app
Memory view can inspect every memory tier, recall across them, and run manager
operations (prune / compact / rebuild / clear). Full paths in decorators.

v11.1.0 adds the Self-Model surface (Track 4) on the same factory: the profile
the Brain holds about its owner is *memory*, so it is read, edited and deleted
here rather than behind a router of its own. Ownership is the whole point —
extraction only ever proposes (``/self-model/propose`` →  Review Center →
``/self-model/apply``), while the user's own edits and deletions write
directly.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from lattice_brain.self_model import SelfModelError
from latticeai.core.messages import http_error, resolve_language
from latticeai.services.memory_service import MemoryService
from latticeai.services.self_model_service import SelfModelService


class RecallRequest(BaseModel):
    query: str = ""
    limit: int = 20


class PruneRequest(BaseModel):
    ids: List[str] = []
    kind: Optional[str] = None


class RebuildRequest(BaseModel):
    target: str = "vector"


class ClearRequest(BaseModel):
    scope: str
    confirm: bool = False


#: Self-Model failure codes → catalog ids. Brain Core raises codes (it cannot
#: import the catalog); the answer a person reads is chosen here, in their
#: language. An unknown code degrades to the generic entry rather than leaking
#: an English developer sentence.
SELF_MODEL_MESSAGES = {
    "invalid_kind": "self_model.invalid_kind",
    "text_required": "self_model.text_required",
    "not_found": "self_model.not_found",
    "not_self_model": "self_model.not_self_model",
    "not_a_proposal": "self_model.not_a_proposal",
    "empty_proposal": "self_model.empty_proposal",
    "graph_unavailable": "self_model.graph_unavailable",
    "queue_unavailable": "self_model.queue_unavailable",
}


def _self_model_error(exc: SelfModelError, request: Request) -> HTTPException:
    status = 404 if exc.code == "not_found" else 400
    message_id = SELF_MODEL_MESSAGES.get(exc.code, "self_model.invalid")
    return http_error(status, message_id, resolve_language(request))


class SelfModelFactRequest(BaseModel):
    """A fact the user states about themselves (user-initiated direct write)."""

    kind: str
    text: str


class SelfModelProposeRequest(BaseModel):
    """Text to read for candidate facts. Output is proposals, never writes."""

    text: str = ""
    source: str = ""
    max_proposals: int = 5


class SelfModelApplyRequest(BaseModel):
    """Approve one Self-Model proposal and write the fact it carries."""

    item_id: str


def create_memory_router(
    *,
    service: MemoryService,
    require_user: Callable[[Request], str],
    get_current_user: Callable[[Request], Optional[str]],
    gate_read: Callable[[Request], Optional[str]],
    gate_write: Callable[[Request], Optional[str]],
    append_audit_event: Callable[..., None],
    active_model_getter: Callable[[], str] | None = None,
    self_model: Any = None,
) -> APIRouter:
    router = APIRouter()
    # Derived from the memory service by default (graph + review queue), so the
    # Self-Model routes exist on every build and report honestly when this
    # Brain has no graph rather than disappearing from the API surface.
    profile = self_model or SelfModelService(memory_service=service)

    @router.get("/api/memory/manager")
    async def memory_manager(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.manager(user_email=user, workspace_id=scope)

    @router.get("/api/memory/brain-quality")
    async def brain_quality_summary(request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.brain_quality_summary(user_email=user, workspace_id=scope)

    @router.get("/api/memory/brain-brief")
    async def brain_brief(request: Request, q: str = "", limit: int = 3):
        user = require_user(request)
        scope = gate_read(request)
        active_model = active_model_getter() if active_model_getter else ""
        return service.brain_brief(
            user_email=user,
            workspace_id=scope,
            active_model=active_model,
            recall_query=q,
            limit=limit,
        )

    @router.get("/api/memory/brain-proof")
    async def brain_proof(request: Request, q: str = "", limit: int = 3):
        user = require_user(request)
        scope = gate_read(request)
        active_model = active_model_getter() if active_model_getter else ""
        return service.brain_proof(
            user_email=user,
            workspace_id=scope,
            active_model=active_model,
            recall_query=q,
            limit=limit,
        )

    @router.get("/api/memory/tiers")
    async def memory_tiers(request: Request):
        require_user(request)
        return service.tiers()

    @router.get("/api/memory/inspect")
    async def memory_inspect(request: Request, source: str, limit: int = 50):
        user = require_user(request)
        scope = gate_read(request)
        try:
            return service.inspect(source, user_email=user, workspace_id=scope, limit=limit)
        except KeyError as exc:
            raise http_error(404, "memory.unknown_source", resolve_language(request), source=source) from exc

    @router.post("/api/memory/recall")
    async def memory_recall(req: RecallRequest, request: Request):
        user = require_user(request)
        scope = gate_read(request)
        return service.recall(req.query, user_email=user, workspace_id=scope, limit=req.limit)

    @router.post("/api/memory/prune")
    async def memory_prune(req: PruneRequest, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        result = service.prune(ids=req.ids, kind=req.kind, user_email=user, workspace_id=scope)
        append_audit_event("memory_prune", user_email=user, count=result.get("count", 0))
        return result

    @router.post("/api/memory/compact")
    async def memory_compact(request: Request):
        user = require_user(request)
        scope = gate_write(request)
        result = service.compact(user_email=user, workspace_id=scope)
        append_audit_event("memory_compact", user_email=user, compacted=result.get("compacted", 0))
        return result

    @router.post("/api/memory/rebuild")
    async def memory_rebuild(req: RebuildRequest, request: Request):
        user = require_user(request)
        gate_write(request)
        result = service.rebuild(req.target)
        append_audit_event("memory_rebuild", user_email=user, target=req.target, status=result.get("status"))
        return result

    @router.post("/api/memory/clear")
    async def memory_clear(req: ClearRequest, request: Request):
        user = require_user(request)
        scope = gate_write(request)
        try:
            result = service.clear(scope=req.scope, confirm=req.confirm, user_email=user, workspace_id=scope)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        append_audit_event("memory_clear", user_email=user, scope=req.scope)
        return result

    # ── Self-Model (v11.1.0) ─────────────────────────────────────────────

    @router.get("/api/memory/self-model")
    async def self_model_profile(request: Request):
        """What the Brain believes about its owner, and the injected summary."""
        user = require_user(request)
        scope = gate_read(request)
        return profile.profile(user_email=user, workspace_id=scope)

    @router.post("/api/memory/self-model")
    async def self_model_upsert(req: SelfModelFactRequest, request: Request):
        """Add or correct one fact directly — the user owns their profile."""
        user = require_user(request)
        scope = gate_write(request)
        try:
            fact = profile.upsert(kind=req.kind, text=req.text, workspace_id=scope)
        except SelfModelError as exc:
            raise _self_model_error(exc, request) from exc
        append_audit_event(
            "self_model_upsert", user_email=user, node_id=fact["id"], kind=fact["kind"]
        )
        return fact

    @router.delete("/api/memory/self-model/{node_id}")
    async def self_model_delete(node_id: str, request: Request):
        """Forget one fact about the user, permanently."""
        user = require_user(request)
        gate_write(request)
        try:
            result = profile.delete(node_id)
        except SelfModelError as exc:
            raise _self_model_error(exc, request) from exc
        append_audit_event("self_model_delete", user_email=user, node_id=node_id)
        return result

    @router.post("/api/memory/self-model/propose")
    async def self_model_propose(req: SelfModelProposeRequest, request: Request):
        """Read text for candidate facts; every hit becomes a review proposal."""
        user = require_user(request)
        scope = gate_write(request)
        result = profile.propose(
            req.text,
            source=req.source or None,
            user_email=user,
            workspace_id=scope,
            max_proposals=req.max_proposals,
        )
        append_audit_event(
            "self_model_proposed",
            user_email=user,
            proposed=result.get("proposed_count", 0),
        )
        return result

    @router.post("/api/memory/self-model/apply")
    async def self_model_apply(req: SelfModelApplyRequest, request: Request):
        """Approve a proposal and write the fact it carries into the graph."""
        user = require_user(request)
        scope = gate_write(request)
        try:
            result = profile.apply(req.item_id, workspace_id=scope)
        except SelfModelError as exc:
            raise _self_model_error(exc, request) from exc
        except (KeyError, FileNotFoundError) as exc:
            raise http_error(404, "review.item_not_found", resolve_language(request)) from exc
        append_audit_event(
            "self_model_applied", user_email=user, item_id=req.item_id
        )
        return result

    return router
