from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from latticeai.services.architecture_readiness import architecture_readiness

PRODUCT_VERSION_TARGET = "14.3.0"


@dataclass(frozen=True)
class ProductGate:
    id: str
    title: str
    evidence: List[str]


PRODUCT_GATES: List[ProductGate] = [
    ProductGate(
        id="first-run",
        title="First five minutes lands without a manual",
        evidence=[
            "docs/ONBOARDING.md::five-minute",
            "frontend/src/components/ProductFlow.tsx::WakeBrainScreen",
            "frontend/src/features/brain/BrainConversation.tsx::BrainHomeDock",
            "frontend/src/features/brain/BrainHomeDock.tsx::BrainBriefPanel",
            "frontend/src/features/brain/BrainHome.tsx",
            "frontend/src/App.tsx::brain-mobile-nav",
            "latticeai/cli/entrypoint.py",
            "latticeai/worker_app.py",
        ],
    ),
    ProductGate(
        id="answer-proof",
        title="Answers carry memory proof and citations",
        evidence=[
            "latticeai/api/search.py::embeddings_status",
            "rust/fixtures/golden/manifest.json",
        ],
    ),
    ProductGate(
        id="action-aware-chat",
        title="File action requests create artifacts instead of code-only answers",
        evidence=[
            "latticeai/api/worker_compute.py::create_worker_compute_router",
            "latticeai/tools/documents.py::read_document",
            "latticeai/tools/filesystem.py::read_file",
            "latticeai/core/tool_governor.py::classify_tool_call",
            "rust/lattice-agent/src/kernel/permission.rs::pub fn block_reason_for_tool",
            "tests/unit/test_worker_compute.py::test_the_docx_bytes_open_as_a_document_with_the_blocks_that_were_sent",
            "tests/unit/test_tool_registry.py::test_execute_tool_uses_registry",
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
            f"README.md::dist/ltcai-{PRODUCT_VERSION_TARGET}-py3-none-any.whl",
            f"README.md::dist/ltcai-{PRODUCT_VERSION_TARGET}.tar.gz",
            f"README.md::dist/ltcai-{PRODUCT_VERSION_TARGET}.vsix",
            f"README.md::ltcai-{PRODUCT_VERSION_TARGET}.tgz",
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
            f"README.md::The current release is **{PRODUCT_VERSION_TARGET}",
            f"SECURITY.md::{'.'.join(PRODUCT_VERSION_TARGET.split('.')[:2])}.x (latest)",
            f"vscode-extension/README.md::**{PRODUCT_VERSION_TARGET}",
            f"docs/CHANGELOG.md::## [{PRODUCT_VERSION_TARGET}]",
            "FEATURE_STATUS.md",
            f"docs/releases/RELEASE_NOTES_v{PRODUCT_VERSION_TARGET}.md",
            "rust/lattice-agent/src/kernel/permission.rs::pub fn block_reason_for_tool",
            "rust/lattice-agent/src/kernel/mod.rs::pub mod agentloop",
            "latticeai/api/agent_worker_seam.py::create_agent_worker_seam_router",
            "rust/lattice-agent/src/kernel/permission.rs::pub fn non_auto_plan_steps",
            "latticeai/worker_app.py::create_worker_app",
            "latticeai/worker_app.py::def create_worker_app",
            "latticeai/services/architecture_readiness.py::lattice-architecture-contract/v1",
            "latticeai/core/tool_governor.py::MUTATING_TOOL_INVENTORY",
        ],
    ),
    ProductGate(
        id="ecosystem-path",
        title="Community and plugin growth path is explicit",
        evidence=[
            f"docs/COMMUNITY_AND_PLUGINS.md::{PRODUCT_VERSION_TARGET}",
            "docs/PLUGIN_SDK.md",
            "plugins/README.md",
            "plugins/hello-world/plugin.json",
        ],
    ),
    ProductGate(
        id="ingestion-graph-coverage",
        title="Graph and ingestion integration coverage guards the Brain",
        evidence=[
            "tests/unit/test_worker_compute_extract.py::test_extract_returns_the_structures_ingestion_consumes",
            "tests/unit/test_cov_p1b_multimodal.py::test_extract_image_facts_unreadable_skipped_and_full_ports",
            "tests/unit/test_t3_vision_embedding.py",
            "tests/unit/test_lattice_brain_isolation.py",
            "rust/lattice-ingest/tests/chunking_parity.rs",
        ],
    ),
    ProductGate(
        id="quality-gates",
        title="Quality is guarded by repeatable gates",
        evidence=[
            "rust/lattice-retrieval/tests/parity.rs",
            "scripts/product_readiness.py",
            "tests/unit/test_product_readiness.py",
            "tests/visual/v3.surfaces.spec.js::Brain Chat Home",
            ".github/workflows/ci.yml::scripts/product_readiness.py",
            ".github/workflows/release.yml::scripts/product_readiness.py",
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
