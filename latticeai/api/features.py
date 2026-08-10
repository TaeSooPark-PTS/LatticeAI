"""Feature switchboard API — read the catalog, move one switch (v11.2.0).

Two routes and no more. The catalog is rendered by the service (labels,
explanations, defaults, which options are even installable), so this router
carries no product knowledge at all: adding a feature is a line in
``latticeai/services/feature_toggles.CATALOG``, not a change here or in the
client.
"""

from __future__ import annotations

from typing import Any, Callable, Union

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from latticeai.core.messages import http_error, resolve_language
from latticeai.services.feature_toggles import (
    FeatureToggleService,
    InvalidFeatureValue,
    UnknownFeature,
)


class SetFeatureRequest(BaseModel):
    """One switch move. ``value`` is a bool for a toggle, a string for a choice."""

    value: Union[bool, str] = Field(
        ..., description="true/false for a toggle, or the option id for a choice"
    )


def create_features_router(
    *,
    service: FeatureToggleService,
    require_user: Callable[..., str],
) -> APIRouter:
    router = APIRouter(tags=["features"])

    @router.get("/api/features")
    async def list_features(request: Request) -> Any:
        """Every opt-in feature, its live value, and where that value came from."""
        require_user(request)
        return service.catalog(resolve_language(request))

    @router.post("/api/features/{feature_id}")
    async def set_feature(
        feature_id: str, body: SetFeatureRequest, request: Request
    ) -> Any:
        """Persist one person's choice; the answer is the feature as it now reads."""
        user = require_user(request)
        language = resolve_language(request)
        try:
            return service.set(
                feature_id, body.value, language=language, user_email=user
            )
        except UnknownFeature as exc:
            raise http_error(
                400, "features.unknown", language, feature=feature_id
            ) from exc
        except InvalidFeatureValue as exc:
            # Pass-through: the service already localized the reason through the
            # same catalog (wrong type, or an option whose optional dependency
            # is not installed), and re-wording it here would only lose it.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


__all__ = ["SetFeatureRequest", "create_features_router"]
