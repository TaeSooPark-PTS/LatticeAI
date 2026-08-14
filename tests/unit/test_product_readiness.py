"""Product and architecture readiness gates still resolve after the worker cut."""

from __future__ import annotations

from pathlib import Path

import latticeai
from latticeai.services.architecture_readiness import (
    _forbidden_patterns,
    _package_sources,
    _symbol_exists,
    architecture_readiness,
    legacy_shim_report,
)
from latticeai.services.product_readiness import (
    PRODUCT_GATES,
    _evidence_resolves,
    product_readiness,
)

REPO = Path(__file__).resolve().parents[2]


def test_product_readiness_is_complete_on_this_tree():
    report = product_readiness(REPO)
    missing = {
        gate["id"]: gate["missing"]
        for gate in report["gates"]
        if gate["missing"]
    }
    assert missing == {}, f"product readiness missing evidence: {missing}"
    assert report["status"] == "complete"
    assert report["architecture"] == "complete"
    # Derived, not a literal: `bump_version.py` rewrites both
    # `PRODUCT_VERSION_TARGET` and `latticeai.__version__`, so pinning the
    # number here only guarantees that every release starts red for a reason
    # that is not a defect. What is worth asserting is that the readiness
    # report speaks for *this* build.
    assert report["version_target"] == latticeai.__version__
    assert report["score"] == f"{len(PRODUCT_GATES)}/{len(PRODUCT_GATES)}"


def test_architecture_readiness_is_complete_on_this_tree():
    report = architecture_readiness(REPO)
    incomplete = [gate["id"] for gate in report["gates"] if gate["status"] != "complete"]
    assert incomplete == [], f"architecture gates incomplete: {incomplete}"
    assert report["status"] == "complete"
    assert report["contract"]["schema_version"] == "lattice-architecture-contract/v1"
    assert report["legacy_compatibility"]["remaining_count"] == 0


def test_product_readiness_defaults_to_the_repo_root():
    report = product_readiness()
    assert report["status"] == "complete"


def test_architecture_readiness_defaults_to_the_repo_root():
    report = architecture_readiness()
    assert report["status"] == "complete"


def test_legacy_shim_report_defaults_to_the_repo_root():
    report = legacy_shim_report()
    assert report["status"] == "managed"
    assert report["lingering"] == []


def test_evidence_resolves_missing_file(tmp_path: Path):
    assert _evidence_resolves(tmp_path, "no/such/file.py") is False
    assert _evidence_resolves(tmp_path, "no/such/file.py::needle") is False


def test_evidence_resolves_needle_in_file(tmp_path: Path):
    target = tmp_path / "note.txt"
    target.write_text("hello world", encoding="utf-8")
    assert _evidence_resolves(tmp_path, "note.txt::hello") is True
    assert _evidence_resolves(tmp_path, "note.txt::missing") is False
    assert _evidence_resolves(tmp_path, "note.txt") is True


def test_architecture_closed_folds_in_an_incomplete_structure(tmp_path: Path, monkeypatch):
    (tmp_path / "latticeai" / "services").mkdir(parents=True)
    (tmp_path / "latticeai" / "services" / "architecture_readiness.py").write_text(
        "ok", encoding="utf-8"
    )
    monkeypatch.setattr(
        "latticeai.services.product_readiness.architecture_readiness",
        lambda _root: {"status": "incomplete", "metrics": {}},
    )
    report = product_readiness(tmp_path)
    closed = next(gate for gate in report["gates"] if gate["id"] == "architecture-closed")
    assert closed["status"] == "incomplete"
    assert "architecture_readiness().status != complete" in closed["missing"]
    assert report["status"] == "incomplete"


def test_symbol_exists_treats_docs_and_missing_as_non_blocking():
    assert _symbol_exists("latticeai.core.config.Config") is True
    assert _symbol_exists("latticeai.core.config.DoesNotExist") is False
    assert _symbol_exists("latticeai.missing_module.Thing") is False
    assert _symbol_exists("notamodule") is False
    assert _symbol_exists("docs/ONBOARDING.md five-minute") is True
    assert _symbol_exists("path::needle") is True
    assert _symbol_exists("pkg.*") is True
    assert _symbol_exists("") is True


def test_package_sources_and_forbidden_patterns(tmp_path: Path):
    assert _package_sources(tmp_path, "missing/pkg") == ["missing/pkg/__init__.py"]
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "one.py").write_text("hello", encoding="utf-8")
    assert _package_sources(tmp_path, "pkg") == ["pkg/one.py"]
    assert _forbidden_patterns(tmp_path, "missing.py", ["x"]) == ["missing:missing.py"]
    (tmp_path / "hit.py").write_text("forbidden token", encoding="utf-8")
    assert _forbidden_patterns(tmp_path, "hit.py", ["forbidden token"]) == ["forbidden token"]
    assert _forbidden_patterns(tmp_path, "hit.py", ["absent"]) == []
