from pathlib import Path

from latticeai.services.product_readiness import PRODUCT_GATES, product_readiness


ROOT = Path(__file__).resolve().parents[2]


def test_v78_product_readiness_is_machine_checkable():
    report = product_readiness(ROOT)
    assert report["version_target"] == "9.0.0"
    assert {gate["id"] for gate in report["gates"]} == {g.id for g in PRODUCT_GATES}
    # Every gate must carry concrete, probeable evidence.
    for gate in report["gates"]:
        assert gate["evidence"], gate["id"]
        assert gate["status"] in {"complete", "incomplete"}


def test_v78_product_is_release_complete():
    report = product_readiness(ROOT)
    incomplete = [g["id"] for g in report["gates"] if g["status"] != "complete"]
    assert report["status"] == "complete", f"incomplete gates: {incomplete}"
    assert report["architecture"] == "complete"
    assert report["metrics"]["product_gates_complete"] == report["metrics"]["product_gates"]


def test_v78_score_reflects_gate_counts():
    report = product_readiness(ROOT)
    complete = sum(1 for g in report["gates"] if g["status"] == "complete")
    assert report["score"] == f"{complete}/{len(report['gates'])}"
