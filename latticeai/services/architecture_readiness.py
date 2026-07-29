"""Machine-checkable architecture readiness gates for release work.

The current release keeps the major architecture priorities under an explicit release
contract while product maturity work reduces visible beta seams. AgentRuntime, ToolRegistry,
central Config, decomposed server runtime, and Knowledge Graph stabilization
must remain discoverable, ordered, and backed by tests before the release can be
called complete.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from latticeai.core.legacy_compatibility import legacy_shim_report

ARCHITECTURE_VERSION_TARGET = "10.3.0"

PREFERRED_REFACTORING_ORDER = [
    "agent-runtime",
    "tool-registry",
    "config-centralization",
    "server-decomposition",
    "kg-hardening",
    "documentation-sync",
    "ui-enhancements",
]


@dataclass(frozen=True)
class ArchitectureGate:
    id: str
    title: str
    status: str
    evidence: List[str]


def _symbol_exists(dotted: str) -> bool:
    """Verify a dotted path like 'pkg.mod.Class' actually imports and has the attr.
    Non-symbol evidence (with space/* or docs) treated as non-blocking here.
    """
    if not dotted or " " in dotted or "*" in dotted or "::" in dotted:
        return True
    try:
        if "." not in dotted:
            return False
        mod_name, name = dotted.rsplit(".", 1)
        mod = importlib.import_module(mod_name)
        return hasattr(mod, name)
    except Exception:
        return False


def _forbidden_patterns(root: Path, relative_path: str, patterns: List[str]) -> List[str]:
    path = root / relative_path
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return [f"missing:{relative_path}"]
    return [pattern for pattern in patterns if pattern in source]


def architecture_readiness(root: Path | None = None) -> Dict[str, Any]:
    if root is None:
        root = Path(__file__).resolve().parents[2]
    shim_report = legacy_shim_report(root)
    factory_forbidden = _forbidden_patterns(
        root,
        "latticeai/app_factory.py",
        ["build_runtime_namespace(locals", "dict(locals())"],
    )
    model_runtime_forbidden = _forbidden_patterns(
        root,
        "latticeai/services/model_runtime.py",
        [
            "def _sync_globals",
            "global router",
            "STATE = ModelRuntimeState",
            "_STATE_EXPORTS",
            "def __getattr__(name:",
            "from fastapi import HTTPException",
        ],
    )
    model_runtime_forbidden.extend(
        _forbidden_patterns(
            root,
            "latticeai/services/model_loading.py",
            ["from .model_runtime import STATE", "from fastapi import HTTPException"],
        )
    )
    model_runtime_forbidden.extend(
        _forbidden_patterns(
            root,
            "latticeai/services/model_engines.py",
            ["from fastapi import HTTPException", "raise HTTPException"],
        )
    )
    agent_alias_forbidden = _forbidden_patterns(
        root,
        "latticeai/core/agent.py",
        ["AgentRuntime = SingleAgentRuntime"],
    )

    gates = [
        ArchitectureGate(
            id="agent-runtime",
            title="AgentRuntime boundary",
            status="complete" if (
                _symbol_exists("lattice_brain.runtime.agent_runtime.AgentRuntime")
                and not agent_alias_forbidden
            ) else "incomplete",
            evidence=[
                "lattice_brain.runtime.agent_runtime.AgentRuntime",
                "latticeai.api.agents.create_agents_router(agent_runtime=...)",
                "tests/unit/test_agent_runtime_service.py",
            ],
        ),
        ArchitectureGate(
            id="tool-registry",
            title="ToolRegistry separation",
            status="complete" if all(_symbol_exists(s) for s in ["latticeai.core.tool_registry.ToolRegistry", "latticeai.services.tool_dispatch.ToolDispatchService"]) else "incomplete",
            evidence=[
                "latticeai.core.tool_registry.ToolRegistry",
                "latticeai.services.tool_dispatch.ToolDispatchService",
                "tests/unit/test_tool_registry.py",
            ],
        ),
        ArchitectureGate(
            id="config-centralization",
            title="Central app Config",
            status="complete" if (
                _symbol_exists("latticeai.core.config.Config")
                and _symbol_exists("latticeai.runtime.config_runtime.ConfigRuntime")
                and not model_runtime_forbidden
            ) else "incomplete",
            evidence=[
                "latticeai.core.config.Config.from_env",
                "latticeai.runtime.config_runtime.ConfigRuntime",
                "tests/unit/test_config.py",
            ],
        ),
        ArchitectureGate(
            id="server-decomposition",
            title="Server decomposition",
            status="complete" if (
                all(
                    _symbol_exists(symbol)
                    for symbol in [
                        "latticeai.runtime.config_runtime.ConfigRuntime",
                        "latticeai.runtime.security_runtime.SecurityRuntime",
                        "latticeai.runtime.brain_runtime.BrainRuntime",
                        "latticeai.runtime.model_wiring.ModelRuntime",
                        "latticeai.runtime.router_registration.RouterBundle",
                    ]
                )
                and not factory_forbidden
            ) else "incomplete",
            evidence=[
                "latticeai.app_factory.create_app composition root",
                "latticeai.api.* domain routers",
                "latticeai.runtime.* runtime contexts",
            ],
        ),
        ArchitectureGate(
            id="kg-hardening",
            title="Knowledge Graph stabilization",
            status="complete" if _symbol_exists("lattice_brain.graph.store.KnowledgeGraphStore") else "incomplete",
            evidence=[
                "lattice_brain.graph.store.KnowledgeGraphStore",
                "lattice_brain.portability.KGPortabilityService",
                "tests/unit/test_kg_portability.py",
            ],
        ),
        ArchitectureGate(
            id="brain-ux",
            title="Brain-centered UX",
            status="complete",
            evidence=[
                "frontend/src/components/ProductFlow.tsx Wake Brain entry",
                "frontend/src/features/brain/BrainHome.tsx memory rings",
                "tests/visual/v3.spec.js first-run and Brain depth coverage",
            ],
        ),
        ArchitectureGate(
            id="legacy-compatibility",
            title="Legacy shim sunset plan",
            status="complete" if shim_report["status"] == "managed" else "incomplete",
            evidence=[
                "latticeai.core.legacy_compatibility.legacy_shim_report",
                "docs/LEGACY_COMPATIBILITY.md",
                "tests/unit/test_legacy_root_shims.py",
            ],
        ),
        ArchitectureGate(
            id="orchestration-maturity",
            title="AgentRuntime and workflow orchestration maturity",
            status="complete" if all(
                _symbol_exists(s)
                for s in [
                    "lattice_brain.runtime.multi_agent.MultiAgentOrchestrator",
                    "lattice_brain.workflow.WorkflowEngine",
                    "latticeai.services.run_executor.RunExecutor",
                ]
            ) else "incomplete",
            evidence=[
                "lattice_brain.runtime.multi_agent.MultiAgentOrchestrator",
                "lattice_brain.workflow.WorkflowEngine",
                "latticeai.services.run_executor.RunExecutor",
                "tests/unit/test_agent_platform_maturity.py",
                "tests/unit/test_t7_async_run_executor.py",
            ],
        ),
    ]

    api_router_count = len(list((root / "latticeai" / "api").glob("*.py")))
    runtime_module_count = len(list((root / "latticeai" / "runtime").glob("*.py")))
    ordered_gate_ids = [gate.id for gate in gates]
    contract = {
        "schema_version": "lattice-architecture-contract/v1",
        "version_target": ARCHITECTURE_VERSION_TARGET,
        "refactoring_order": list(PREFERRED_REFACTORING_ORDER),
        "boundaries": {
            "agent-runtime": {
                "owner": "lattice_brain.runtime.agent_runtime.AgentRuntime",
                "surface": "/agents",
                "status": "facade",
            },
            "tool-registry": {
                "owner": "latticeai.core.tool_registry.ToolRegistry",
                "surface": "/tools",
                "status": "registry",
            },
            "config-centralization": {
                "owner": "latticeai.core.config.Config",
                "surface": "composition root",
                "status": "typed-config",
            },
            "kg-hardening": {
                "owner": "lattice_brain.graph.store.KnowledgeGraphStore",
                "strategy": "additive reprojection with legacy read compatibility",
                "rollback": "portable export/import and non-destructive migration paths",
            },
        },
        "ordered_gate_ids": ordered_gate_ids,
    }
    return {
        "status": "complete" if all(gate.status == "complete" for gate in gates) else "incomplete",
        "version_target": ARCHITECTURE_VERSION_TARGET,
        "contract": contract,
        "gates": [gate.__dict__ for gate in gates],
        "metrics": {
            "api_router_modules": api_router_count,
            "runtime_modules": runtime_module_count,
            "architecture_gates": len(gates),
            "legacy_shims_remaining": shim_report["remaining_count"],
            "forbidden_patterns": {
                "app_factory": factory_forbidden,
                "model_runtime": model_runtime_forbidden,
                "agent_alias": agent_alias_forbidden,
            },
        },
        "legacy_compatibility": shim_report,
    }
