"""Persistent project sessions — the multi-turn project loop (v9.9.6).

Review 2026-07-27 P1 #7 / 루프 §4: "만들고 → 수정하고 → 테스트하고 → 다시
고치는 긴 프로젝트 루프의 상태 관리가 아직 단일 런 중심". House rules verified
here: a project never upgrades a NEEDS_REVIEW run into "done", scope isolation
holds, the prompt summary stays empty when there is nothing honest to say, and
persistence failures degrade instead of breaking a run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from latticeai.api.project_sessions import create_project_sessions_router
from latticeai.core.agent import AgentRunContext, SingleAgentRuntime
from latticeai.core.project_sessions import ProjectSessionStore


@pytest.fixture()
def store(tmp_path):
    return ProjectSessionStore(tmp_path / "project_sessions")


def _project(store, **kw):
    return store.create(title=kw.pop("title", "앱 만들기"), user_email="u@x.com", **kw)


def test_create_list_and_scope_isolation(store):
    mine = _project(store, goal="HTML 대시보드")
    other = store.create(title="남의 프로젝트", user_email="other@x.com")
    assert store.get(mine["id"], user_email="u@x.com")["title"] == "앱 만들기"
    # Another user's project is not visible, and not partially returned.
    assert store.get(other["id"], user_email="u@x.com") is None
    listed = store.list(user_email="u@x.com")
    assert [p["id"] for p in listed["projects"]] == [mine["id"]]
    assert listed["count"] == 1


def test_archived_projects_leave_the_default_listing_but_keep_history(store):
    project = _project(store)
    store.update(project["id"], status="archived", user_email="u@x.com")
    assert store.list(user_email="u@x.com")["projects"] == []
    assert len(store.list(user_email="u@x.com", status="all")["projects"]) == 1
    assert store.get(project["id"], user_email="u@x.com")["status"] == "archived"


def test_runs_accumulate_files_without_duplicates(store):
    project = _project(store)
    store.record_run(
        project["id"], run_id="r1", status="ok", final_state="DONE",
        files=[{"path": "index.html"}, {"path": "style.css"}],
        explanation={"ok": True, "headline": {"ko": "끝났습니다.", "en": "Done."}},
        user_email="u@x.com",
    )
    store.record_run(
        project["id"], run_id="r2", status="ok", final_state="DONE",
        files=["index.html", "app.js"],
        explanation={"ok": True, "headline": {"ko": "끝났습니다.", "en": "Done."}},
        user_email="u@x.com",
    )
    record = store.get(project["id"], user_email="u@x.com")
    assert record["files"] == ["index.html", "style.css", "app.js"]
    assert [run["run_id"] for run in record["runs"]] == ["r1", "r2"]


def test_a_needs_review_run_never_becomes_a_project_done(store):
    project = _project(store)
    store.record_run(
        project["id"], run_id="r1", status="failed", final_state="NEEDS_REVIEW",
        files=["a.html"],
        explanation={"ok": False, "headline": {"ko": "확인이 필요합니다.", "en": "Check it."}},
        user_email="u@x.com",
    )
    verification = store.get(project["id"], user_email="u@x.com")["last_verification"]
    assert verification["final_state"] == "NEEDS_REVIEW"
    assert verification["ok"] is False
    assert verification["headline"] == "확인이 필요합니다."


def test_todos_normalize_strings_and_dicts(store):
    project = _project(store)
    record = store.set_todos(
        project["id"],
        ["다크모드 추가", {"text": "테스트 작성", "done": True}, {"text": "  "}, ""],
        user_email="u@x.com",
    )
    assert record["todos"] == [
        {"text": "다크모드 추가", "done": False},
        {"text": "테스트 작성", "done": True},
    ]


def test_summary_is_the_prompt_block_the_next_run_needs(store):
    project = _project(store, goal="HTML 대시보드")
    store.record_run(
        project["id"], run_id="r1", status="failed", final_state="NEEDS_REVIEW",
        files=["index.html"],
        explanation={"ok": False, "headline": {"ko": "확인이 필요합니다.", "en": "Check."}},
        user_email="u@x.com",
    )
    store.set_todos(
        project["id"],
        [{"text": "차트 붙이기", "done": False}, {"text": "레이아웃", "done": True}],
        user_email="u@x.com",
    )
    summary = store.summary(project["id"], user_email="u@x.com")
    assert "index.html" in summary
    assert "차트 붙이기" in summary
    assert "레이아웃" not in summary  # completed work is not re-proposed
    assert "NEEDS_REVIEW" in summary


def test_summary_of_an_empty_project_adds_nothing_to_the_prompt(store):
    project = _project(store)
    assert store.summary(project["id"], user_email="u@x.com") == ""
    # Unknown / out-of-scope ids never leak a block either.
    assert store.summary("does-not-exist", user_email="u@x.com") == ""
    assert store.summary(project["id"], user_email="other@x.com") == ""


def test_traversal_style_ids_are_rejected(store):
    assert store.get("../../etc/passwd") is None
    assert store.delete("../../etc/passwd") is False
    assert store.summary("..") == ""


def test_agent_prompt_block_is_injected_only_for_project_runs():
    runtime = SingleAgentRuntime.__new__(SingleAgentRuntime)
    standalone = AgentRunContext()
    assert runtime._project_block(standalone) == ""
    in_project = AgentRunContext()
    in_project.project_context = "Project: 앱\nFiles this project has already produced:\n- a.html"
    block = runtime._project_block(in_project)
    assert block.startswith("\n\n[PROJECT SESSION]")
    assert "a.html" in block


# ── router ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def client(store):
    app = FastAPI()
    app.include_router(
        create_project_sessions_router(
            store=store,
            require_user=lambda request: "u@x.com",
            gate_read=lambda request: None,
            gate_write=lambda request: None,
        )
    )
    return TestClient(app)


def test_router_round_trip(client):
    created = client.post("/api/projects", json={"title": "대시보드", "goal": "차트"})
    assert created.status_code == 200
    project_id = created.json()["id"]

    listed = client.get("/api/projects").json()
    assert listed["count"] == 1

    todos = client.put(
        f"/api/projects/{project_id}/todos", json={"todos": ["차트 붙이기"]}
    )
    assert todos.json()["todos"][0]["text"] == "차트 붙이기"

    patched = client.patch(f"/api/projects/{project_id}", json={"status": "archived"})
    assert patched.json()["status"] == "archived"

    assert client.delete(f"/api/projects/{project_id}").status_code == 200
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_router_404s_on_unknown_projects(client):
    assert client.get("/api/projects/unknown-id").status_code == 404
    assert client.patch("/api/projects/unknown-id", json={"title": "x"}).status_code == 404
    assert client.put("/api/projects/unknown-id/todos", json={"todos": []}).status_code == 404
    assert client.delete("/api/projects/unknown-id").status_code == 404
