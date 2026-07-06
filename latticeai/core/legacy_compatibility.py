"""Managed legacy compatibility surface for legacy import shims.

Compatibility modules are intentionally still present for old scripts, VS Code
extension paths, historical integrations, and pre-graph-package imports. 8.4.0
stops treating them as vague technical debt: every tracked shim has an owner,
migration target, removal phase, and replacement import that can be surfaced in
docs and tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


LEGACY_COMPATIBILITY_VERSION = "8.9.0"


@dataclass(frozen=True)
class LegacyShim:
    path: str
    owner: str
    replacement: str
    reason: str
    removal_phase: str
    layer: str = "root"
    status: str = "managed"

    def as_dict(self) -> Dict[str, str]:
        return {
            "path": self.path,
            "owner": self.owner,
            "replacement": self.replacement,
            "reason": self.reason,
            "removal_phase": self.removal_phase,
            "layer": self.layer,
            "status": self.status,
        }


LEGACY_SHIMS: List[LegacyShim] = [
    LegacyShim(
        path="knowledge_graph.py",
        owner="lattice_brain.graph",
        replacement="from lattice_brain.graph.store import KnowledgeGraphStore",
        reason="Historical scripts imported the graph store from the repo root.",
        removal_phase="major-release-after-8.x",
    ),
    LegacyShim(
        path="knowledge_graph_api.py",
        owner="latticeai.api.knowledge_graph",
        replacement="from latticeai.api.knowledge_graph import create_knowledge_graph_router",
        reason="Older API composition roots imported the knowledge graph router from the repo root.",
        removal_phase="major-release-after-8.x",
    ),
    LegacyShim(
        path="kg_schema.py",
        owner="lattice_brain.graph.schema",
        replacement="from lattice_brain.graph.schema import ...",
        reason="Historical graph schema tests and tools referenced root schema symbols.",
        removal_phase="major-release-after-8.x",
    ),
    LegacyShim(
        path="ltcai_cli.py",
        owner="latticeai.cli.entrypoint",
        replacement="from latticeai.cli.entrypoint import main",
        reason="Console entry points and older package installs imported the CLI root module.",
        removal_phase="major-release-after-8.x",
    ),
    LegacyShim(
        path="telegram_bot.py",
        owner="latticeai.integrations.telegram_bot",
        replacement="from latticeai.integrations.telegram_bot import run_bot",
        reason="Existing Telegram automation setups still import the legacy root module.",
        removal_phase="major-release-after-8.x",
    ),
    LegacyShim(
        path="p_reinforce.py",
        owner="latticeai.services.p_reinforce",
        replacement="from latticeai.services.p_reinforce import PReinforceGardener",
        reason="Old gardener scripts referenced the root reinforcement helper.",
        removal_phase="major-release-after-8.x",
    ),
    LegacyShim(
        path="server.py",
        owner="latticeai.server_app",
        replacement="from latticeai.server_app import app, main",
        reason="Deployment docs and local launch scripts historically targeted server.py.",
        removal_phase="major-release-after-8.x",
    ),
    LegacyShim(
        path="local_knowledge_api.py",
        owner="latticeai.api.local_files",
        replacement="from latticeai.api.local_files import create_local_files_router",
        reason="Local folder ingestion integrations used the root local knowledge API.",
        removal_phase="requires-api-route-migration",
    ),
]

# Shim layers that have completed their compatibility window and were
# physically deleted. Kept in the report so consumers (docs, tests, release
# notes) can tell "removed on purpose" apart from "missing by accident".
# Removing the internal-only layers is the first step of extracting
# ``lattice_brain`` as a standalone Brain Core package: the package now has
# exactly one import surface (``lattice_brain.*`` physical paths).
REMOVED_SHIMS: List[LegacyShim] = [
    LegacyShim(
        path="lattice_brain/store.py",
        owner="lattice_brain.graph.store",
        replacement="from lattice_brain.graph.store import KnowledgeGraphStore",
        reason="Pre-graph-package flat modules were internal-only; removed in 8.8.0.",
        removal_phase="removed-8.8.0",
        layer="brain-flat",
        status="removed",
    ),
    LegacyShim(
        path="lattice_brain/ingest.py",
        owner="lattice_brain.graph.ingest",
        replacement="from lattice_brain.graph.ingest import KnowledgeGraphIngestMixin",
        reason="Pre-graph-package flat modules were internal-only; removed in 8.8.0.",
        removal_phase="removed-8.8.0",
        layer="brain-flat",
        status="removed",
    ),
    LegacyShim(
        path="lattice_brain/retrieval.py",
        owner="lattice_brain.graph.retrieval",
        replacement="from lattice_brain.graph.retrieval import KnowledgeGraphRetrievalMixin",
        reason="Pre-graph-package flat modules were internal-only; removed in 8.8.0.",
        removal_phase="removed-8.8.0",
        layer="brain-flat",
        status="removed",
    ),
    LegacyShim(
        path="latticeai/brain/",
        owner="lattice_brain",
        replacement="import lattice_brain",
        reason="The deprecated latticeai.brain namespace completed its window; removed in 8.8.0.",
        removal_phase="removed-8.8.0",
        layer="deprecated-namespace",
        status="removed",
    ),
    LegacyShim(
        path="latticeai/services/agent_runtime.py",
        owner="lattice_brain.runtime.agent_runtime",
        replacement="from lattice_brain.runtime.agent_runtime import AgentRuntime",
        reason="The service-layer alias completed its window; removed in 8.8.0.",
        removal_phase="removed-8.8.0",
        layer="service-alias",
        status="removed",
    ),
]


def legacy_shim_report(root: Path | None = None) -> Dict[str, Any]:
    if root is None:
        root = Path(__file__).resolve().parents[2]
    entries = [shim.as_dict() for shim in LEGACY_SHIMS]
    missing = [shim.path for shim in LEGACY_SHIMS if not (root / shim.path).exists()]
    lingering = [shim.path for shim in REMOVED_SHIMS if (root / shim.path).exists()]
    phases = sorted({shim.removal_phase for shim in LEGACY_SHIMS})
    layers = sorted({shim.layer for shim in LEGACY_SHIMS})
    return {
        "schema_version": "legacy-compatibility/v1",
        "version_target": LEGACY_COMPATIBILITY_VERSION,
        "status": "managed" if not missing and not lingering else "incomplete",
        "remaining_count": len(LEGACY_SHIMS),
        "removed_count": len(REMOVED_SHIMS),
        "missing": missing,
        "lingering": lingering,
        "removal_phases": phases,
        "layers": layers,
        "shims": entries,
        "removed": [shim.as_dict() for shim in REMOVED_SHIMS],
    }


__all__ = [
    "LEGACY_COMPATIBILITY_VERSION",
    "LEGACY_SHIMS",
    "REMOVED_SHIMS",
    "LegacyShim",
    "legacy_shim_report",
]
