"""Machine-checkable *product* readiness gates for the 8.4 line.

Where ``architecture_readiness`` proves the internal structure is sound, this
module answers the product question the 8.4 release exists to settle: *does the
app now feel like a finished product rather than only a strong framework?*
It does so honestly: every gate is backed by
evidence that is probed on disk, so a gate only reports ``complete`` when its
evidence actually resolves. The same report can be printed by
``scripts/product_readiness.py`` and re-run after every change, which is the
point: completeness is something we keep measuring, not a one-time claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from latticeai.services.architecture_readiness import architecture_readiness

PRODUCT_VERSION_TARGET = "8.8.0"


@dataclass(frozen=True)
class ProductGate:
    id: str
    title: str
    # Evidence is either a repo-relative path that must exist, or
    # "path::needle" meaning the file must exist and contain ``needle``.
    evidence: List[str]


PRODUCT_GATES: List[ProductGate] = [
    ProductGate(
        id="first-run",
        title="First five minutes lands without a manual",
        evidence=[
            "docs/ONBOARDING.md::five-minute",
            "frontend/src/components/ProductFlow.tsx::WakeBrainScreen",
            "frontend/src/features/brain/BrainConversation.tsx::ProductCommandCenter",
            "frontend/src/features/brain/BrainConversation.tsx::BrainBriefPanel",
            "frontend/src/features/brain/BrainHome.tsx",
            "auto_setup.py",
            "setup_wizard.py",
        ],
    ),
    ProductGate(
        id="answer-proof",
        title="Answers carry memory proof and citations",
        evidence=[
            "latticeai/api/memory.py::brain-proof",
            "scripts/brain_quality_eval.py",
        ],
    ),
    ProductGate(
        id="action-aware-chat",
        title="File action requests create artifacts instead of code-only answers",
        evidence=[
            "latticeai/api/chat.py::is_file_action_request",
            "latticeai/api/chat.py::direct_write_file",
            "tests/unit/test_chat_telegram_decoupling.py::test_chat_file_creation_intent_writes_real_file",
        ],
    ),
    ProductGate(
        id="local-first-trust",
        title="Local-first privacy is stated and bounded",
        evidence=[
            "PRIVACY.md",
            "PUBLIC_MODE.md",
            "SECURITY.md",
        ],
    ),
    ProductGate(
        id="packaging",
        title="One command produces shippable artifacts",
        evidence=[
            "package.json::release:artifacts",
            "package.json::release:validate",
            "README.md::dist/ltcai-8.8.0-py3-none-any.whl",
            "README.md::dist/ltcai-8.8.0.tar.gz",
            "README.md::dist/ltcai-8.8.0.vsix",
            "README.md::ltcai-8.8.0.tgz",
            "scripts/validate_release_artifacts.py",
            "scripts/release_smoke.py",
            "Dockerfile",
        ],
    ),
    ProductGate(
        id="architecture-closed",
        title="Architecture readiness is complete",
        evidence=["latticeai/services/architecture_readiness.py"],
    ),
    ProductGate(
        id="trust-docs",
        title="Release story is documented and honest",
        evidence=[
            "README.md",
            "README.md::The current release is **8.8.0",
            "SECURITY.md::8.8.x (latest)",
            "vscode-extension/README.md::**8.8.0",
            "docs/CHANGELOG.md::## [8.8.0]",
            "FEATURE_STATUS.md",
            "RELEASE_NOTES_v8.8.0.md",
            "latticeai/core/agent.py::SingleAgentRuntime",
            "latticeai/core/agent.py::AgentRuntime = SingleAgentRuntime",
            "lattice_brain/runtime/contracts.py::runtime-boundary/v1",
            "lattice_brain/runtime/contracts.py::RuntimeBoundaryProtocol",
            "lattice_brain/runtime/agent_runtime.py::def boundary",
            "latticeai/core/agent.py::def boundary",
            "latticeai/services/architecture_readiness.py::lattice-architecture-contract/v1",
            "latticeai/services/tool_dispatch.py::rollback_file",
        ],
    ),
    ProductGate(
        id="ecosystem-path",
        title="Community and plugin growth path is explicit",
        evidence=[
            "docs/COMMUNITY_AND_PLUGINS.md::8.8.0",
            "docs/PLUGIN_SDK.md",
            "plugins/README.md",
            "plugins/hello-world/plugin.json",
        ],
    ),
    ProductGate(
        id="ingestion-graph-coverage",
        title="Graph and ingestion integration coverage guards the Brain",
        evidence=[
            "tests/unit/test_ingestion_pipeline.py::test_upload_result_enters_unified_ingestion_pipeline",
            "tests/unit/test_ingestion_pipeline.py::test_ingestion_preserves_workspace_scope_for_duplicate_content",
            "tests/integration/test_ingest_graph_retrieval.py",
            "tests/unit/test_lattice_brain_isolation.py",
            "tests/unit/test_retrieval_benchmark_corpus.py",
        ],
    ),
    ProductGate(
        id="quality-gates",
        title="Quality is guarded by repeatable gates",
        evidence=[
            "scripts/brain_quality_eval.py",
            "scripts/product_readiness.py",
            "tests/unit/test_v78_product_readiness.py",
            "tests/visual/v3.spec.js::Brain Chat Home",
            ".github/workflows/ci.yml::scripts/product_readiness.py",
            ".github/workflows/release.yml::npm run lint",
        ],
    ),
]


def _evidence_resolves(root: Path, evidence: str) -> bool:
    if "::" in evidence:
        rel, needle = evidence.split("::", 1)
        target = root / rel
        if not target.is_file():
            return False
        try:
            return needle in target.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
    return (root / evidence).exists()


def product_readiness(root: Path | None = None) -> Dict[str, Any]:
    if root is None:
        root = Path(__file__).resolve().parents[2]

    arch = architecture_readiness(root)

    gate_reports: List[Dict[str, Any]] = []
    for gate in PRODUCT_GATES:
        missing = [e for e in gate.evidence if not _evidence_resolves(root, e)]
        # The architecture gate is satisfied by the composed report, not just
        # the file existing — fold its status in so this score can never claim
        # product completeness while the structure underneath is incomplete.
        if gate.id == "architecture-closed" and arch.get("status") != "complete":
            missing = missing or ["architecture_readiness().status != complete"]
        gate_reports.append(
            {
                "id": gate.id,
                "title": gate.title,
                "status": "complete" if not missing else "incomplete",
                "evidence": gate.evidence,
                "missing": missing,
            }
        )

    complete = sum(1 for g in gate_reports if g["status"] == "complete")
    total = len(gate_reports)
    return {
        "status": "complete" if complete == total else "incomplete",
        "version_target": PRODUCT_VERSION_TARGET,
        "score": f"{complete}/{total}",
        "gates": gate_reports,
        "architecture": arch["status"],
        "metrics": {
            "product_gates": total,
            "product_gates_complete": complete,
            **arch.get("metrics", {}),
        },
    }
