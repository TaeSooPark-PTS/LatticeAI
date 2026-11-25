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

    return ModelRuntimeService(create_model_runtime_state(**deps))


@dataclass(slots=True)
class ModelRuntimeService:
    state: ModelRuntimeState
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

    return ModelRuntimeService(create_model_runtime_state(**deps))
