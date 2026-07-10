from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from latticeai.runtime.review_wiring import build_review_run_now_runner


def test_review_run_now_runner_uses_payload_workflow_and_review_item_input():
    calls = []
    platform = SimpleNamespace(
        run_workflow_by_id=lambda *args, **kwargs: calls.append((args, kwargs))
        or {"run": {"id": "run-1"}}
    )
    runner = build_review_run_now_runner(platform, HTTPException)

    result = runner(
        {"id": "review-1", "payload": {"workflow_id": "wf-payload"}},
        user_email="u@example.com",
        scope="personal",
    )

    assert result == {"run": {"id": "run-1"}}
    assert calls == [
        (
            ("wf-payload", "u@example.com", "personal"),
            {"with_agent": True, "inputs": {"__review_item__": "review-1"}},
        )
    ]


def test_review_run_now_runner_falls_back_to_provenance_workflow():
    calls = []
    platform = SimpleNamespace(
        run_workflow_by_id=lambda *args, **kwargs: calls.append((args, kwargs)) or "run-2"
    )
    runner = build_review_run_now_runner(platform, HTTPException)

    assert runner(
        {"id": "review-2", "provenance": {"workflow_id": "wf-prov"}},
        user_email=None,
        scope=None,
    ) == "run-2"
    assert calls[0][0] == ("wf-prov", None, None)


def test_review_run_now_runner_rejects_items_without_workflow():
    runner = build_review_run_now_runner(SimpleNamespace(), HTTPException)

    with pytest.raises(HTTPException) as exc:
        runner({"id": "review-missing"}, user_email="u@example.com", scope="personal")

    assert exc.value.status_code == 409
    assert exc.value.detail == "review item has no workflow to run"
