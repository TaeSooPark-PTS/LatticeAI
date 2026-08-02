"""Review Center runtime wiring helpers."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional


def build_review_run_now_runner(
    platform: Any,
    # Injected as `fastapi.HTTPException`, which takes (status_code, detail) —
    # not the bare `Exception` signature the narrower annotation implied.
    http_exception: Callable[..., Exception],
) -> Callable[..., Any]:
    """Build the Review Center run-now runner used by the API router.

    The runner preserves the public contract: "Run now" previews/regenerates the
    source workflow, records a fresh run id, and leaves approval status unchanged.
    """

    def run_review_item(
        item: Dict[str, Any],
        *,
        user_email: Optional[str],
        scope: Optional[str],
    ) -> Any:
        payload = item.get("payload") or {}
        provenance = item.get("provenance") or {}
        workflow_id = payload.get("workflow_id") or provenance.get("workflow_id")
        if not workflow_id:
            raise http_exception(status_code=409, detail="review item has no workflow to run")
        return platform.run_workflow_by_id(
            workflow_id,
            user_email,
            scope,
            with_agent=True,
            inputs={"__review_item__": item.get("id")},
        )

    return run_review_item


__all__ = ["build_review_run_now_runner"]
