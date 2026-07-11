"""Setup services owned by the :mod:`latticeai` package."""

from .auto_setup import (
    InstallPlan,
    Recommendation,
    SystemProfile,
    plan,
    preset,
    probe,
    recommend,
    run_all,
    verify,
)

__all__ = [
    "InstallPlan",
    "Recommendation",
    "SystemProfile",
    "plan",
    "preset",
    "probe",
    "recommend",
    "run_all",
    "verify",
]
