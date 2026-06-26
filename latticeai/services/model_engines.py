"""Engine server management and install logic extracted from model_runtime.

Re-exports keep the legacy module surface identical for configure + callers.
"""
from __future__ import annotations

from typing import Any, Dict


# Avoid circular: use local imports inside functions or minimal deps.


def ensure_lmstudio_server() -> None:
    # Delegated from model_runtime; full logic remains co-located for stability in this pass.
    # Extraction started here for future wave.
    pass


def ensure_ollama_server() -> None:
    pass


def ensure_vllm_server(model_name: str) -> None:
    pass


def ensure_llamacpp_server(model_name: str) -> None:
    pass


def engine_support_status(engine: str) -> Dict[str, object]:
    # moved stub; real impl can live here
    return {"supported": True, "reason": None}


def install_engine(engine: str) -> Dict[str, Any]:
    # extraction target for install logic
    return {"status": "not_implemented_in_stub", "engine": engine}


# Re-export common for convenience
__all__ = [
    "ensure_lmstudio_server",
    "ensure_ollama_server",
    "ensure_vllm_server",
    "ensure_llamacpp_server",
    "engine_support_status",
    "install_engine",
]
