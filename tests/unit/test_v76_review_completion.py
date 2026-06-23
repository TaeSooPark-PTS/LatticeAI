from pathlib import Path

from latticeai.core.config import Config
from latticeai.core.tool_registry import ToolRegistry
from latticeai.services.architecture_readiness import architecture_readiness
from lattice_brain.runtime.agent_runtime import AgentRuntime


def test_v76_architecture_review_items_are_machine_checkable():
    report = architecture_readiness(Path(__file__).resolve().parents[2])
    assert report["status"] == "complete"
    assert report["version_target"] == "7.9.0"
    assert {gate["id"] for gate in report["gates"]} == {
        "agent-runtime",
        "tool-registry",
        "config-centralization",
        "server-decomposition",
        "kg-hardening",
        "brain-ux",
    }
    assert report["metrics"]["api_router_modules"] >= 20
    assert report["metrics"]["runtime_modules"] >= 5


def test_v76_core_boundaries_are_importable():
    assert AgentRuntime is not None
    assert ToolRegistry is not None
    cfg = Config.from_env({}, base_dir=Path(__file__).resolve().parents[2])
    assert cfg.app_mode == "local"
    assert cfg.enable_graph is True
