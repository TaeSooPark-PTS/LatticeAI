from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from latticeai.runtime.build_phases import BUILD_PHASES
from latticeai.runtime.runtime_context import RuntimeContext

if TYPE_CHECKING:
    from latticeai.core.config import Config


def build_context(config: "Optional[Config]" = None) -> RuntimeContext:
    ctx = RuntimeContext(config)
    for phase in BUILD_PHASES:
        phase(ctx)
    return ctx


__all__ = ["build_context"]
