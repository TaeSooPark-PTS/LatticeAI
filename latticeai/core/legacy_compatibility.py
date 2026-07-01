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


LEGACY_COMPATIBILITY_VERSION = "8.4.0"


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
        replacement="from lattice_brain.store import KnowledgeGraphStore",
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
        replacement="from lattice_brain.schema import ...",
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
    LegacyShim(
        path="lattice_brain/store.py",
        owner="lattice_brain.graph.store",
        replacement="from lattice_brain.graph.store import KnowledgeGraphStore",
        reason="Pre-graph-package imports used the flat Brain store module.",
        removal_phase="major-release-after-8.x",
        layer="brain-flat",
    ),
    LegacyShim(
        path="lattice_brain/ingest.py",
        owner="lattice_brain.graph.ingest",
        replacement="from lattice_brain.graph.ingest import KnowledgeGraphIngestMixin",
        reason="Pre-graph-package imports used the flat Brain ingest module.",
        removal_phase="major-release-after-8.x",
        layer="brain-flat",
    ),
    LegacyShim(
        path="lattice_brain/retrieval.py",
        owner="lattice_brain.graph.retrieval",
        replacement="from lattice_brain.graph.retrieval import KnowledgeGraphRetrievalMixin",
        reason="Pre-graph-package imports used the flat Brain retrieval module.",
        removal_phase="major-release-after-8.x",
        layer="brain-flat",
    ),
    LegacyShim(
        path="latticeai/brain/store.py",
        owner="lattice_brain.graph.store",
        replacement="from lattice_brain.graph.store import KnowledgeGraphStore",
        reason="The deprecated latticeai.brain namespace remains for package users.",
        removal_phase="major-release-after-8.x",
        layer="deprecated-namespace",
    ),
    LegacyShim(
        path="latticeai/brain/ingest.py",
        owner="lattice_brain.graph.ingest",
        replacement="from lattice_brain.graph.ingest import KnowledgeGraphIngestMixin",
        reason="The deprecated latticeai.brain namespace remains for package users.",
        removal_phase="major-release-after-8.x",
        layer="deprecated-namespace",
    ),
    LegacyShim(
        path="latticeai/services/agent_runtime.py",
        owner="lattice_brain.runtime.agent_runtime",
        replacement="from lattice_brain.runtime.agent_runtime import AgentRuntime",
        reason="Service-layer imports existed before AgentRuntime moved into Brain runtime.",
        removal_phase="major-release-after-8.x",
        layer="service-alias",
    ),
]


def legacy_shim_report(root: Path | None = None) -> Dict[str, Any]:
    if root is None:
        root = Path(__file__).resolve().parents[2]
    entries = [shim.as_dict() for shim in LEGACY_SHIMS]
    missing = [shim.path for shim in LEGACY_SHIMS if not (root / shim.path).exists()]
    phases = sorted({shim.removal_phase for shim in LEGACY_SHIMS})
    layers = sorted({shim.layer for shim in LEGACY_SHIMS})
    return {
        "schema_version": "legacy-compatibility/v1",
        "version_target": LEGACY_COMPATIBILITY_VERSION,
        "status": "managed" if not missing else "incomplete",
        "remaining_count": len(LEGACY_SHIMS),
        "missing": missing,
        "removal_phases": phases,
        "layers": layers,
        "shims": entries,
    }


__all__ = [
    "LEGACY_COMPATIBILITY_VERSION",
    "LEGACY_SHIMS",
    "LegacyShim",
    "legacy_shim_report",
]
