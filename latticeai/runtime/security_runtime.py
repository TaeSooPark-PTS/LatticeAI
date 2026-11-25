from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from latticeai.runtime.stages import RuntimeStage

if TYPE_CHECKING:
    from latticeai.core.config import Config


@dataclass(frozen=True)
class SecurityRuntime(RuntimeStage):
    RATE_LIMIT_ENABLED: bool


def build_security_runtime(config: "Config") -> SecurityRuntime:

    return SecurityRuntime(RATE_LIMIT_ENABLED=config.rate_limit_enabled)


__all__ = ["SecurityRuntime", "build_security_runtime"]
