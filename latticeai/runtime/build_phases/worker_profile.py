from __future__ import annotations

from typing import Any, List, Set, Tuple

WORKER_ROUTES: Tuple[Tuple[str, str], ...] = (
    ("POST", "/agent/llm"),
    ("POST", "/agent/tool"),
    ("GET", "/health"),
    ("GET", "/api/embeddings/status"),
    ("GET", "/models"),
    ("POST", "/models/load"),
    ("DELETE", "/models/unload/{model_id:path}"),
    ("POST", "/engines/prepare-model"),
    ("POST", "/engines/prepare-model/stream"),
)

GRAPH_WRITER_ROUTES: Tuple[Tuple[str, str], ...] = ()

WORKER_SEAM_ROUTES: Tuple[Tuple[str, str], ...] = (
    ("GET", "/worker/sysinfo"),
    ("POST", "/worker/llm/stream"),
)

WORKER_COMPUTE_ROUTES: Tuple[Tuple[str, str], ...] = (
    ("POST", "/worker/embed"),
    ("POST", "/worker/parse"),
    ("POST", "/worker/render/docx"),
    ("POST", "/worker/render/xlsx"),
    ("POST", "/worker/render/pptx"),
    ("POST", "/worker/render/pdf"),
    ("POST", "/worker/asr"),
    ("POST", "/worker/extract"),
    ("POST", "/worker/vector/query"),
    ("POST", "/worker/rerank"),
)


def worker_route_keys() -> Set[Tuple[str, str]]:
    return (
        set(WORKER_ROUTES)
        | set(GRAPH_WRITER_ROUTES)
        | set(WORKER_SEAM_ROUTES)
        | set(WORKER_COMPUTE_ROUTES)
    )


def _route_keys(route: Any) -> Set[Tuple[str, str]]:
    return {(str(method), str(route.path)) for method in route.methods}


def phase_worker_routes(ctx: Any) -> None:
    from latticeai.api.worker_compute import create_worker_compute_router
    from latticeai.api.worker_resilience import install_worker_compute_resilience
    from latticeai.api.worker_seams import create_worker_seams_router
    from latticeai.core.rerankers import resolve_reranker, warm_reranker

    app = ctx.app
    install_worker_compute_resilience(app)
    app.include_router(
        create_worker_seams_router(
            require_user=ctx.require_user,
            enforce_rate_limit=ctx.enforce_rate_limit,
            model_router=getattr(ctx, "model_router", None),
        )
    )
    ports = getattr(ctx, "MULTIMODAL_PORTS", None)
    data_dir = getattr(ctx, "DATA_DIR", None)
    db_path = None
    if data_dir is not None:
        from pathlib import Path

        db_path = Path(data_dir) / "knowledge_graph.sqlite"
    reranker = resolve_reranker()
    app.include_router(
        create_worker_compute_router(
            embedder=getattr(ctx, "EMBEDDER", None),
            transcriber=getattr(ports, "transcriber", None),
            require_user=ctx.require_user,
            enforce_rate_limit=ctx.enforce_rate_limit,
            db_path=db_path,
            reranker=reranker,
        )
    )
    apply_worker_route_filter(app)
    warm_reranker(reranker)


def _included_router(route: Any) -> Any:
    inner = getattr(route, "original_router", None)
    return inner if inner is not None and hasattr(inner, "routes") else None


def apply_worker_route_filter(app: Any) -> None:
    from fastapi.routing import APIRoute
    from starlette.routing import Mount

    wanted = worker_route_keys()
    present: Set[Tuple[str, str]] = set()

    def _prune(routes: List[Any]) -> List[Any]:
        kept: List[Any] = []
        for route in list(routes):
            included = _included_router(route)
            if included is not None:
                included.routes = _prune(included.routes)
                if included.routes:
                    kept.append(route)
                continue
            if isinstance(route, APIRoute):
                keys = _route_keys(route)
                if keys <= wanted:
                    present.update(keys)
                    kept.append(route)
                continue
            if isinstance(route, Mount):
                continue
            kept.append(route)
        return kept

    kept = _prune(app.router.routes)
    missing = sorted(wanted - present)
    if missing:
        raise RuntimeError(
            "worker profile: the build produced no route for "
            + ", ".join(f"{method} {path}" for method, path in missing)
        )
    app.router.routes = kept
    app.openapi_schema = None


__all__ = [
    "GRAPH_WRITER_ROUTES",
    "WORKER_COMPUTE_ROUTES",
    "WORKER_ROUTES",
    "WORKER_SEAM_ROUTES",
    "apply_worker_route_filter",
    "phase_worker_routes",
    "worker_route_keys",
]
