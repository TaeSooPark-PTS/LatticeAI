from __future__ import annotations

from latticeai.runtime.build_phases.features import phase_features as phase_features
from latticeai.runtime.build_phases.foundation import phase_brain as phase_brain
from latticeai.runtime.build_phases.foundation import phase_config as phase_config
from latticeai.runtime.build_phases.foundation import phase_identity as phase_identity
from latticeai.runtime.build_phases.foundation import phase_platform as phase_platform
from latticeai.runtime.build_phases.web import phase_domain as phase_domain
from latticeai.runtime.build_phases.web import phase_web as phase_web

BUILD_PHASES = (
    phase_platform,
    phase_config,
    phase_identity,
    phase_brain,
    phase_domain,
    phase_web,
    phase_features,
)


__all__ = [
    "BUILD_PHASES",
    "phase_brain",
    "phase_config",
    "phase_domain",
    "phase_features",
    "phase_identity",
    "phase_platform",
    "phase_web",
]
