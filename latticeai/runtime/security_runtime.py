"""Security-derived runtime settings for the AI-Worker build.

Everything this module used to resolve — SSO endpoints, the invitation gate's
per-install secrets, the Secure-cookie decision — belonged to the login surface
``lattice-auth`` owns now. One setting outlives it: whether the per-user rate
limiter is armed, which the worker still enforces on every seam call.
"""

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
    """Build the security-derived runtime settings from the central config."""

    return SecurityRuntime(RATE_LIMIT_ENABLED=config.rate_limit_enabled)


__all__ = ["SecurityRuntime", "build_security_runtime"]
