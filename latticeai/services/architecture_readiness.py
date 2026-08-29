"""Machine-checkable architecture readiness gates for release work.

The current release keeps the major architecture priorities under an explicit
release contract. Agent permission gates, ToolRegistry, central Config, the
worker-only composition root, and the compute-side Knowledge Graph helpers
must remain discoverable, ordered, and backed by tests.

v11.6.0 retargeted every symbol probe at the worker-only tree: the product
HTTP surface, the Python agent loop, the graph writer and the orchestration
runtimes now live in Rust. Assertions were not deleted — their paths and
symbols now name the modules that still own those concerns in Python, or the
worker seam that is the Python half of a native owner.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

ARCHITECTURE_VERSION_TARGET = "12.2.0"

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


def _package_sources(root: Path, relative_package: str) -> List[str]:
    """Every ``*.py`` in a package, as repo-relative paths.

    Returns the missing marker path when the package is absent, so a gate that
    scans a package fails the same way it would for a deleted single file.
    """
    directory = root / relative_package
    sources = sorted(directory.glob("*.py")) if directory.is_dir() else []
    if not sources:
        return [f"{relative_package}/__init__.py"]
    return [str(path.relative_to(root)) for path in sources]


def _forbidden_patterns(root: Path, relative_path: str, patterns: List[str]) -> List[str]:
    path = root / relative_path
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return [f"missing:{relative_path}"]
    return [pattern for pattern in patterns if pattern in source]


def legacy_shim_report(root: Path | None = None) -> Dict[str, Any]:
    """Sunset report for the root shim layer.

    ``latticeai.core.legacy_compatibility`` was deleted with the product
    server. The remaining fact this gate still has to prove is: no root
    ``*.py`` is left, including ``server.py``.
    """
    if root is None:
        root = Path(__file__).resolve().parents[2]
    lingering = sorted(path.name for path in root.glob("*.py"))
    removed = [
        "ltcai_cli.py",
        "auto_setup.py",
        "setup_wizard.py",
        "mcp_registry.py",
        "kg_schema.py",
        "knowledge_graph.py",
        "knowledge_graph_api.py",
        "local_knowledge_api.py",
        "llm_router.py",
        "p_reinforce.py",
        "telegram_bot.py",
        "tools/",
        "server.py",
    ]
    return {
        "status": "managed" if not lingering else "incomplete",
        "remaining_count": len(lingering),
        "shims": [],
        "removed": [{"path": path} for path in removed],
        "missing": [],
        "lingering": lingering,
    }


def architecture_readiness(root: Path | None = None) -> Dict[str, Any]:
    if root is None:
        root = Path(__file__).resolve().parents[2]
    shim_report = legacy_shim_report(root)
    factory_forbidden = _forbidden_patterns(
        root,
        "latticeai/app_factory.py",
        ["build_runtime_namespace(locals", "dict(locals())"],
    )
    # v11.3.0: model_runtime.py became a package. The ambient-state patterns
    # below could reappear in any of its submodules, so every file in the
    # package is scanned — checking only __init__.py would leave the gate
    # blind one directory down.
    model_runtime_forbidden = [
        finding
        for relative_path in _package_sources(root, "latticeai/services/model_runtime")
        for finding in _forbidden_patterns(
            root,
            relative_path,
            [
                "def _sync_globals",
                "global router",
                "STATE = ModelRuntimeState",
                "_STATE_EXPORTS",
                "def __getattr__(name:",
                "from fastapi import HTTPException",
            ],
        )
    ]
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
    # The Python agent loop package is gone, and 11.8.0 removed the last of its
    # gate tables (``core/agent_permission.py``) once the Rust goldens froze.
    # The compatibility alias is scanned on the one Python file that still
    # talks about permission mode: the worker seam the native loop calls.
    agent_alias_forbidden = _forbidden_patterns(
        root,
        "latticeai/api/agent_worker_seam.py",
        ["AgentRuntime = SingleAgentRuntime"],
    )

    gates = [
        ArchitectureGate(
            id="agent-runtime",
            title="AgentRuntime boundary",
            status="complete" if (
                _symbol_exists(
                    "latticeai.api.agent_worker_seam.create_agent_worker_seam_router"
                )
                and not agent_alias_forbidden
            ) else "incomplete",
            evidence=[
                "rust/lattice-agent/src/kernel/permission.rs block_reason_for_tool (native owner)",
                "latticeai.api.agent_worker_seam.create_agent_worker_seam_router",
                "tests/unit/test_agent_worker_seam.py",
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
                "tests/unit/test_runtime_context.py",
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
                        "latticeai.runtime.runtime_context.RuntimeContext",
                        "latticeai.worker_app.create_worker_app",
                        "latticeai.app_factory.build_context",
                    ]
                )
                and not factory_forbidden
            ) else "incomplete",
            evidence=[
                "latticeai.worker_app.create_worker_app composition root",
                "latticeai.api.* domain routers",
                "latticeai.runtime.* runtime contexts",
            ],
        ),
        ArchitectureGate(
            id="kg-hardening",
            title="Knowledge Graph stabilization",
            status="complete" if _symbol_exists("lattice_brain.embeddings.LocalEmbeddingModel") else "incomplete",
            evidence=[
                "lattice_brain.embeddings.LocalEmbeddingModel",
                "lattice_brain.graph._kg_common.extraction",
                "tests/unit/test_kg_common_exports.py",
            ],
        ),
        ArchitectureGate(
            id="brain-ux",
            title="Brain-centered UX",
            status="complete",
            evidence=[
                "frontend/src/components/ProductFlow.tsx Wake Brain entry",
                "frontend/src/features/brain/BrainHome.tsx memory rings",
                "tests/visual/v3.home.spec.js first-run and Brain depth coverage",
            ],
        ),
        ArchitectureGate(
            id="legacy-compatibility",
            title="Legacy shim sunset plan",
            status="complete" if shim_report["status"] == "managed" else "incomplete",
            evidence=[
                # The standalone `check_legacy_debt.mjs` scanner retired once
                # the shim layer hit zero; `legacy_shim_report` *is* the check
                # now, and its status is what this gate reads.
                "latticeai.services.architecture_readiness.legacy_shim_report",
                "tests/unit/test_legacy_root_shims.py",
            ],
        ),
        ArchitectureGate(
            id="orchestration-maturity",
            title="AgentRuntime and workflow orchestration maturity",
            status="complete" if all(
                _symbol_exists(s)
                for s in [
                    "latticeai.services.tool_dispatch.ToolDispatchService",
                    "latticeai.tools.execute_tool",
                    "latticeai.api.worker_compute.create_worker_compute_router",
                ]
            ) else "incomplete",
            evidence=[
                "latticeai.services.tool_dispatch.ToolDispatchService",
                "latticeai.tools.execute_tool",
                "latticeai.api.worker_compute.create_worker_compute_router",
                "tests/unit/test_tool_registry.py",
                "tests/unit/test_worker_compute.py",
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
                "owner": "lattice-agent::permission::block_reason_for_tool",
                "surface": "/agent/llm + /agent/tool",
                "status": "worker-seam",
            },
            "tool-registry": {
                "owner": "latticeai.core.tool_registry.ToolRegistry",
                "surface": "/agent/tool",
                "status": "registry",
            },
            "config-centralization": {
                "owner": "latticeai.core.config.Config",
                "surface": "composition root",
                "status": "typed-config",
            },
            "kg-hardening": {
                "owner": "lattice_brain.embeddings.LocalEmbeddingModel",
                "strategy": "compute-only embedder and _kg_common helpers; writes are native",
                "rollback": "committed rust/fixtures goldens; Python generators frozen at fc65e60",
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
