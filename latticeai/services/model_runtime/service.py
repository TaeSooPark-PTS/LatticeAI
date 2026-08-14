"""The bound runtime service — one application's model operations.

Everything above is a free function taking an explicit ``state``. This is the
object the composition root holds: it carries that state plus the only piece of
per-application operational data (the cloud verification cache), so a second
ASGI app in the same process starts with an empty cache rather than inheriting
probe results it never ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from latticeai.services.model_runtime.loading import (
    prepare_and_load_model,
    prepare_and_load_model_stream,
)
from latticeai.services.model_runtime.state import (
    ModelRuntimeState,
    create_model_runtime_state,
)
from latticeai.services.model_runtime.status import engine_status, runtime_features


def configure_model_runtime(**deps: Any) -> "ModelRuntimeService":
    """Compatibility factory returning an isolated, bound runtime service.

    The historical function mutated process-wide module globals.  Keeping the
    import path while returning a service preserves practical construction
    compatibility without ambient state or cross-application leakage.
    """

    return ModelRuntimeService(create_model_runtime_state(**deps))

@dataclass(slots=True)
class ModelRuntimeService:
    """Bound model operations for one explicitly configured application.

    All configuration and app-owned callables live on ``state``. Operational
    verification cache data belongs to this service instance, so creating a
    second ASGI app cannot inherit credentials, routers, or probe results from
    the first one.
    """

    state: ModelRuntimeState
    #: Engine probes cache their cloud-provider answers here. It is per-service
    #: rather than process-wide so a second application starts with an empty
    #: cache instead of inheriting probe results it never ran.
    _cloud_verify_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def runtime_features(self) -> Dict[str, Any]:
        return runtime_features(state=self.state)

    def engine_status(self) -> List[Dict[str, Any]]:
        return engine_status(
            state=self.state,
            cloud_verify_cache=self._cloud_verify_cache,
        )


    async def prepare_and_load_model(
        self,
        model_id: str,
        request: Any,
        engine: Optional[str] = None,
        user_email: Optional[str] = None,
        adapter_path: Optional[str] = None,
        draft_model_id: Optional[str] = None,
        allow_download: bool = False,
    ) -> Dict[str, Any]:
        return await prepare_and_load_model(
            model_id,
            request,
            engine=engine,
            user_email=user_email,
            adapter_path=adapter_path,
            draft_model_id=draft_model_id,
            allow_download=allow_download,
            state=self.state,
        )

    async def prepare_and_load_model_stream(
        self,
        model_id: str,
        request: Any,
        engine: Optional[str] = None,
        user_email: Optional[str] = None,
        allow_download: bool = False,
    ) -> AsyncIterator[str]:
        async for event in prepare_and_load_model_stream(
            model_id,
            request,
            engine=engine,
            user_email=user_email,
            allow_download=allow_download,
            state=self.state,
        ):
            yield event


def build_model_runtime(**deps: Any) -> ModelRuntimeService:
    """Build the application's isolated model runtime service."""

    return ModelRuntimeService(create_model_runtime_state(**deps))
