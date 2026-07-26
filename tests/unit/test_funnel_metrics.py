"""UX funnel metrics tests (backlog #16).

Counter math, JSON persistence across instances, thread-safe increments,
rate/TTFV derivation, and the admin-gated endpoint.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.funnel_metrics import create_funnel_metrics_router
from latticeai.services.funnel_metrics import COUNTER_NAMES, FunnelMetricsService


def _service(tmp_path) -> FunnelMetricsService:
    return FunnelMetricsService(tmp_path / "funnel_metrics.json")


# ── counter math + persistence ──────────────────────────────────────────

def test_counters_start_at_zero_and_increment(tmp_path):
    service = _service(tmp_path)
    snapshot = service.snapshot()
    assert set(snapshot["counters"]) == set(COUNTER_NAMES)
    assert all(value == 0 for value in snapshot["counters"].values())

    service.increment("file_requests")
    service.increment("file_requests")
    service.increment("real_file_delivered")
    counters = service.snapshot()["counters"]
    assert counters["file_requests"] == 2
    assert counters["real_file_delivered"] == 1


def test_unknown_counter_and_bad_step_are_ignored(tmp_path):
    service = _service(tmp_path)
    service.increment("not_a_counter")
    service.increment("file_requests", by=0)
    service.increment("file_requests", by=-3)
    service.increment("file_requests", by="nan")  # type: ignore[arg-type]
    counters = service.snapshot()["counters"]
    assert counters["file_requests"] == 1  # bad step coerced to 1
    assert "not_a_counter" not in counters


def test_state_persists_across_instances(tmp_path):
    service = _service(tmp_path)
    service.increment("file_requests")
    service.record_ingest()
    service.record_recall_success()

    reopened = _service(tmp_path)
    snapshot = reopened.snapshot()
    assert snapshot["counters"]["file_requests"] == 1
    assert snapshot["counters"]["ingest_completions"] == 1
    assert snapshot["counters"]["recall_successes"] == 1
    assert snapshot["firsts"]["first_ingest_at"]
    assert snapshot["firsts"]["first_value_at"]


def test_corrupt_metrics_file_starts_fresh(tmp_path):
    path = tmp_path / "funnel_metrics.json"
    path.write_text("{not json", encoding="utf-8")
    service = FunnelMetricsService(path)
    assert service.snapshot()["counters"]["file_requests"] == 0
    service.increment("file_requests")
    assert json.loads(path.read_text(encoding="utf-8"))["file_requests"] == 1


def test_threaded_increments_do_not_lose_counts(tmp_path):
    service = _service(tmp_path)

    def bump():
        for _ in range(25):
            service.increment("agent_runs")

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert service.snapshot()["counters"]["agent_runs"] == 100


# ── rate + TTFV derivation ──────────────────────────────────────────────

def test_rates_are_none_without_denominator(tmp_path):
    rates = _service(tmp_path).snapshot()["rates"]
    assert rates["real_file_rate"] is None
    assert rates["code_only_rate"] is None
    assert rates["needs_review_rate"] is None


def test_rate_derivation(tmp_path):
    service = _service(tmp_path)
    for _ in range(4):
        service.increment("file_requests")
    for _ in range(3):
        service.increment("real_file_delivered")
    service.increment("code_only_responses")
    for _ in range(2):
        service.increment("agent_runs")
    service.increment("needs_review_runs")
    rates = service.snapshot()["rates"]
    assert rates["real_file_rate"] == 0.75
    assert rates["code_only_rate"] == 0.25
    assert rates["needs_review_rate"] == 0.5


def test_ttfv_requires_ingest_before_value(tmp_path):
    service = _service(tmp_path)
    # A grounded answer before any ingest does not start the value clock.
    service.record_recall_success()
    assert service.snapshot()["ttfv_seconds"] is None
    assert service.snapshot()["firsts"]["first_value_at"] is None

    service.record_ingest()
    service.record_recall_success()
    snapshot = service.snapshot()
    assert snapshot["firsts"]["first_ingest_at"]
    assert snapshot["firsts"]["first_value_at"]
    assert snapshot["ttfv_seconds"] is not None
    assert snapshot["ttfv_seconds"] >= 0


def test_ttfv_derives_from_stored_timestamps(tmp_path):
    path = tmp_path / "funnel_metrics.json"
    path.write_text(json.dumps({
        "ingest_completions": 1,
        "recall_successes": 1,
        "first_ingest_at": "2026-07-21T10:00:00+00:00",
        "first_value_at": "2026-07-21T10:00:42+00:00",
    }), encoding="utf-8")
    assert FunnelMetricsService(path).snapshot()["ttfv_seconds"] == 42.0


def test_first_timestamps_are_never_overwritten(tmp_path):
    path = tmp_path / "funnel_metrics.json"
    path.write_text(json.dumps({
        "first_ingest_at": "2026-07-01T00:00:00+00:00",
    }), encoding="utf-8")
    service = FunnelMetricsService(path)
    service.record_ingest()
    assert (
        service.snapshot()["firsts"]["first_ingest_at"]
        == "2026-07-01T00:00:00+00:00"
    )


def test_approval_counters_exist_and_rate_is_none_without_pauses(tmp_path):
    service = _service(tmp_path)
    snapshot = service.snapshot()
    assert snapshot["counters"]["approval_pauses"] == 0
    assert snapshot["counters"]["approval_resumes"] == 0
    # Honest rate: no pause has happened yet, so there is no denominator.
    assert snapshot["rates"]["approval_resume_rate"] is None


def test_approval_resume_rate_derivation_and_persistence(tmp_path):
    service = _service(tmp_path)
    for _ in range(4):
        service.increment("approval_pauses")
    for _ in range(3):
        service.increment("approval_resumes")
    assert service.snapshot()["rates"]["approval_resume_rate"] == 0.75

    reopened = _service(tmp_path)
    snapshot = reopened.snapshot()
    assert snapshot["counters"]["approval_pauses"] == 4
    assert snapshot["counters"]["approval_resumes"] == 3
    assert snapshot["rates"]["approval_resume_rate"] == 0.75


# ── endpoint (admin gated) ──────────────────────────────────────────────

def _client(tmp_path):
    service = _service(tmp_path)

    def require_admin(request: Request):
        if request.headers.get("X-Role") != "admin":
            raise HTTPException(status_code=403, detail="admin only")
        return "admin@example.com", {}

    app = FastAPI()
    app.include_router(create_funnel_metrics_router(
        service=service, require_admin=require_admin,
    ))
    return TestClient(app), service


def test_endpoint_requires_admin(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/admin/funnel-metrics").status_code == 403


def test_endpoint_returns_snapshot(tmp_path):
    client, service = _client(tmp_path)
    service.increment("file_requests")
    service.increment("real_file_delivered")
    r = client.get("/api/admin/funnel-metrics", headers={"X-Role": "admin"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counters"]["file_requests"] == 1
    assert body["rates"]["real_file_rate"] == 1.0
    assert "ttfv_seconds" in body
    assert body["generated_at"]


# ── Funnel → decision (review 2026-07-27 P2 #10) ─────────────────────────────
# "soft gate는 있으나 '제품이 실제로 나빠졌을 때' 알림/대시보드 연결이 약함".
# Rates now become named, actionable alerts — and stay silent below the
# sample floor, because an alert nobody can trust gets ignored.

def _alerts(**counters):
    from latticeai.services.funnel_metrics import funnel_alerts

    base = {name: 0 for name in COUNTER_NAMES}
    base.update(counters)
    rates = {
        "real_file_rate": (
            base["real_file_delivered"] / base["file_requests"]
            if base["file_requests"] else None
        ),
        "code_only_rate": (
            base["code_only_responses"] / base["file_requests"]
            if base["file_requests"] else None
        ),
        "needs_review_rate": (
            base["needs_review_runs"] / base["agent_runs"] if base["agent_runs"] else None
        ),
        "approval_resume_rate": (
            base["approval_resumes"] / base["approval_pauses"]
            if base["approval_pauses"] else None
        ),
    }
    return {alert["key"] for alert in funnel_alerts(base, rates)}


def test_healthy_funnel_raises_no_alerts():
    assert _alerts(
        file_requests=20, real_file_delivered=20,
        agent_runs=20, needs_review_runs=1,
        approval_pauses=20, approval_resumes=18,
        ingest_completions=5, recall_successes=4,
    ) == set()


def test_a_degraded_file_pipeline_is_named():
    keys = _alerts(file_requests=20, real_file_delivered=10, code_only_responses=10)
    assert "real_file_rate_low" in keys
    assert "code_only_rate_high" in keys


def test_a_high_needs_review_rate_is_named():
    assert "needs_review_rate_high" in _alerts(agent_runs=20, needs_review_runs=10)


def test_unresumed_approvals_are_named():
    assert "approval_resume_rate_low" in _alerts(approval_pauses=20, approval_resumes=2)


def test_ingested_but_never_recalled_is_named():
    assert "no_grounded_recall" in _alerts(ingest_completions=3, recall_successes=0)


def test_alerts_stay_silent_below_the_sample_floor():
    # Two bad runs out of two is not evidence of a regression.
    assert _alerts(file_requests=2, real_file_delivered=0, code_only_responses=2) == set()
    assert _alerts(agent_runs=3, needs_review_runs=3) == set()
    assert _alerts(approval_pauses=2, approval_resumes=0) == set()


def test_alerts_carry_the_number_that_triggered_them(tmp_path):
    from latticeai.services.funnel_metrics import funnel_alerts

    alerts = funnel_alerts(
        {name: 0 for name in COUNTER_NAMES} | {"agent_runs": 20, "needs_review_runs": 10},
        {"needs_review_rate": 0.5},
    )
    assert alerts[0]["value"] == 0.5
    assert alerts[0]["samples"] == 20
    assert alerts[0]["ko"] and alerts[0]["en"]


def test_snapshot_exposes_alerts_to_the_admin_surface(tmp_path):
    service = FunnelMetricsService(tmp_path / "funnel.json")
    for _ in range(20):
        service.increment("agent_runs")
        service.increment("needs_review_runs")
    snapshot = service.snapshot()
    assert {alert["key"] for alert in snapshot["alerts"]} == {"needs_review_rate_high"}
