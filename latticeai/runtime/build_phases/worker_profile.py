"""The worker profile: which routes an AI-Worker process serves, and only those.

v11.6.0 turns ``lattice-host`` into the product server (plan §설계 결정 1): 439
of the 464 Python routes become native axum handlers, and the proxy fall-through
is inverted into an allowlist that passes **only** the worker's own surface. The
allowlist and this module's :data:`WORKER_ROUTES` are the same contract read from
opposite ends, so it is written down once, here, as data.

Two honest boundaries, because a reader will otherwise assume more:

* **This profile shrinks the HTTP surface, not the construction.** The routers
  the worker keeps are built by the same ten phases the product app runs — the
  document tools, the model lifecycle and the embedding routes are assembled
  deep inside ``phase_platform_features`` / ``phase_interaction``, and pulling
  them out is module surgery that belongs to WP-P1 (plan §Wave 3), not to an
  additive seam. What this module guarantees today is the contract: a worker
  answers the remaining product routes plus the three state ``/worker``
  seams (record-turn stays until WP-P1) plus the eight compute seams and
  nothing else, so the Rust allowlist can be written against it now.
  When P1 deletes the keep-set's complement, the filter below stops removing
  anything and becomes an assertion that nothing crept back.

* **The filter fails loudly.** A route named here that construction did not
  produce raises at boot instead of yielding a worker that is quietly missing
  its embedding surface. That is the failure mode a later WP will actually hit.

The browser middleware (CORS, the CSRF origin guard) and the ``/static`` +
``/icons`` mounts stay on the application object — they are attached by
``phase_web`` — but the UI routes they serve are gone from the worker, which is
correct: the worker is a loopback process the host starts for itself, and every
seam on it is still behind ``require_user`` plus the seam switch.
"""

from __future__ import annotations

from typing import Any, List, Set, Tuple

#: ``(method, path)`` for each of the 25 routes v11.6.0 leaves in Python —
#: docs/v11.6.0_ONE_DOOR_PLAN.md §최종 Python 워커 표면, cross-checked against
#: the KEEP_WORKER table of the route scout. Paths are FastAPI's, converters
#: included, so this compares to ``APIRoute.path`` without normalisation.
WORKER_ROUTES: Tuple[Tuple[str, str], ...] = (
    # AI-Worker seam (v11.5.1)
    ("POST", "/agent/llm"),
    ("POST", "/agent/tool"),
    ("POST", "/agent/change-proposal"),
    # supervisor liveness + access posture
    ("GET", "/health"),
    # embedding production (drain/rebuild are native — W3b)
    ("GET", "/api/embeddings/status"),
    ("GET", "/api/embeddings/providers"),
    # MLX model + engine lifecycle (in-process only)
    ("GET", "/models"),
    ("POST", "/models/load"),
    ("POST", "/models/switch/{model_id:path}"),
    ("DELETE", "/models/unload/{model_id:path}"),
    ("DELETE", "/models/unload-all"),
    ("POST", "/engines/prepare-model"),
    ("POST", "/engines/prepare-model/stream"),
    # document parser (create_* + upload are native — W3b)
    ("POST", "/tools/read_document"),
    ("GET", "/tools/pdf_pages"),
    ("GET", "/api/ingestion/multimodal"),
    # local ASR status (POST /api/capture/voice is native — W3b)
    ("GET", "/api/capture/voice/status"),
)

#: Retired by W3b — ingest is native. Kept as an empty tuple so imports
#: (`GRAPH_WRITER_ROUTES`) and `worker_route_keys()` stay stable.
GRAPH_WRITER_ROUTES: Tuple[Tuple[str, str], ...] = ()

#: The four seams v11.6.0 adds (WP-I6 + WP-R4's streaming completion).
#: Mounted here and nowhere else.
WORKER_SEAM_ROUTES: Tuple[Tuple[str, str], ...] = (
    ("POST", "/worker/chat/record-turn"),
    ("GET", "/worker/sysinfo"),
    ("POST", "/worker/llm/stream"),
)

#: The pure-compute seams of Wave 2.5 §W2 (``latticeai/api/worker_compute.py``).
#: Kept apart from :data:`WORKER_SEAM_ROUTES` because they age in the opposite
#: direction: the state seams above are retired once §W3 lands the native write
#: engine, while these eight are what the worker is *for* once Rust owns every
#: write. Mounted here and nowhere else.
WORKER_COMPUTE_ROUTES: Tuple[Tuple[str, str], ...] = (
    ("POST", "/worker/embed"),
    ("POST", "/worker/parse"),
    ("POST", "/worker/render/docx"),
    ("POST", "/worker/render/xlsx"),
    ("POST", "/worker/render/pptx"),
    ("POST", "/worker/render/pdf"),
    ("POST", "/worker/asr"),
    ("POST", "/worker/multimodal/describe"),
)


def worker_route_keys() -> Set[Tuple[str, str]]:
    """Every ``(method, path)`` a worker process may answer."""
    return (
        set(WORKER_ROUTES)
        | set(GRAPH_WRITER_ROUTES)
        | set(WORKER_SEAM_ROUTES)
        | set(WORKER_COMPUTE_ROUTES)
    )


def build_worker_history_deps(ctx: Any) -> Any:
    """The history dependency bundle, wired exactly as ``phase_brain`` wires it.

    ``save_to_history`` builds the same bundle per call inside a closure, which
    a router cannot reach. Rebuilt here from the same context attributes so the
    seam writes through the same store, the same audit sink and the same
    ingestion door — if one of them ever moves, both sites read ``ctx`` and move
    together.
    """
    from lattice_brain.ingestion import IngestionItem
    from latticeai.models.router import normalize_branding
    from latticeai.runtime.history_writer import HistoryWriterDeps

    return HistoryWriterDeps(
        conversations=ctx.CONVERSATIONS,
        append_audit_event=ctx.append_audit_event,
        classify_sensitive_message=ctx.classify_sensitive_message,
        redact_secret_text=ctx.redact_secret_text,
        normalize_branding=normalize_branding,
        ingestion_pipeline=ctx.INGESTION_PIPELINE,
        ingestion_item_factory=IngestionItem,
        enable_graph=ctx.ENABLE_GRAPH,
        knowledge_graph=ctx.KNOWLEDGE_GRAPH,
    )


def _route_keys(route: Any) -> Set[Tuple[str, str]]:
    """``{(method, path)}`` for one API route — every method it answers.

    A route is kept only when *all* of its methods are on the allowlist, so a
    path that also serves an un-allowlisted verb cannot slip through on the
    strength of the one verb the worker wanted.
    """
    return {(str(method), str(route.path)) for method in route.methods}


def phase_worker_routes(ctx: Any) -> None:
    """Mount the v11.6.0 seams, then reduce the app to the worker surface.

    Runs *after* the ordinary build phases: it needs the change governor, the
    conversation store and the knowledge graph, all of which land in phases 4
    and 9. It publishes nothing onto the context — the profile is a decision
    about the application object, not more assembly state.
    """
    from latticeai.api.worker_compute import create_worker_compute_router
    from latticeai.api.worker_seams import create_worker_seams_router

    app = ctx.app
    app.include_router(
        create_worker_seams_router(
            history_deps=build_worker_history_deps(ctx),
            graph_store=ctx.KNOWLEDGE_GRAPH if ctx.ENABLE_GRAPH else None,
            require_user=ctx.require_user,
            enforce_rate_limit=ctx.enforce_rate_limit,
            model_router=getattr(ctx, "model_router", None),
        )
    )
    # The compute seams take the *resolved* embedder and the same injected
    # multimodal ports the ingestion path holds, so a vector or a caption
    # produced through HTTP is the one this process would have produced
    # in-process. ``getattr`` because a worker built without a Brain still boots
    # — each seam reports the absence rather than failing construction.
    ports = getattr(ctx, "MULTIMODAL_PORTS", None)
    app.include_router(
        create_worker_compute_router(
            embedder=getattr(ctx, "EMBEDDER", None),
            transcriber=getattr(ports, "transcriber", None),
            multimodal_ports=ports,
            require_user=ctx.require_user,
            enforce_rate_limit=ctx.enforce_rate_limit,
        )
    )
    apply_worker_route_filter(app)


def apply_worker_route_filter(app: Any) -> None:
    """Keep the worker's routes and FastAPI's own doc routes; drop the rest.

    ``/openapi.json`` survives because the worker-only schema is one half of
    the v11.6.0 OpenAPI composer (plan §설계 결정 4); the ``/static`` and
    ``/icons`` mounts do not, because a worker serves no UI.
    """
    from fastapi.routing import APIRoute
    from starlette.routing import Mount

    wanted = worker_route_keys()
    kept: List[Any] = []
    present: Set[Tuple[str, str]] = set()
    for route in list(app.router.routes):
        if isinstance(route, APIRoute):
            keys = _route_keys(route)
            if keys <= wanted:
                present |= keys
                kept.append(route)
            continue
        if isinstance(route, Mount):
            # ``/static`` and ``/icons``: a worker serves no UI.
            continue
        # FastAPI's own ``/openapi.json``, ``/docs``, ``/redoc``.
        kept.append(route)
    missing = sorted(wanted - present)
    if missing:
        raise RuntimeError(
            "worker profile: the build produced no route for "
            + ", ".join(f"{method} {path}" for method, path in missing)
        )
    app.router.routes = kept
    # The schema was never generated at this point, but a caller that asked for
    # it before pruning would otherwise be served the product route table.
    app.openapi_schema = None


__all__ = [
    "GRAPH_WRITER_ROUTES",
    "WORKER_COMPUTE_ROUTES",
    "WORKER_ROUTES",
    "WORKER_SEAM_ROUTES",
    "apply_worker_route_filter",
    "build_worker_history_deps",
    "phase_worker_routes",
    "worker_route_keys",
]
