"""Closing the three open agent loops (v9.9.6).

Review 2026-07-27 §4 "루프 엔지니어링":

1. **재검색 루프** — a file written moments ago must be visible to the next
   turn even before asynchronous indexing catches up.
2. **Critic의 의미 검증** — "did this produce what was actually requested?"
   is now a deterministic fact for declared files, not only critic prose.
3. **실패 학습 루프** — a failed run leaves a structured "do this differently"
   that the next run in the same project actually reads.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lattice_brain.context import ContextAssembler
from latticeai.core.agent import requirement_coverage
from latticeai.core.artifact_ledger import ArtifactLedger
from latticeai.core.project_sessions import ProjectSessionStore
from latticeai.core.run_explain import explain_run

FILE_ACTIONS = frozenset({"write_file", "generate_file"})


def _write_step(path: str) -> dict:
    return {
        "state": "EXECUTING",
        "action": "write_file",
        "args": {"path": path},
        "result": {"path": path},
    }


# ── 1. re-search loop ────────────────────────────────────────────────────────


def test_ledger_returns_only_this_conversation_artifacts():
    ledger = ArtifactLedger()
    ledger.record(["index.html"], user_email="u@x.com", conversation_id="c1")
    ledger.record(["other.html"], user_email="u@x.com", conversation_id="c2")
    mine = ledger.recent(user_email="u@x.com", conversation_id="c1")
    assert [item["path"] for item in mine] == ["index.html"]
    # Unknown conversation: honest absence, never a cross-conversation leak.
    assert ledger.recent(user_email="u@x.com", conversation_id="c3") == []
    assert ledger.recent(user_email="other@x.com", conversation_id="c1") == []


def test_ledger_accepts_both_artifact_shapes_and_dedupes():
    ledger = ArtifactLedger()
    ledger.record(["a.html", {"path": "b.css"}], user_email="u", conversation_id="c")
    ledger.record([{"path": "a.html"}], user_email="u", conversation_id="c")
    paths = [item["path"] for item in ledger.recent(user_email="u", conversation_id="c")]
    assert sorted(paths) == ["a.html", "b.css"]


def test_ledger_is_bounded_per_conversation_and_overall():
    ledger = ArtifactLedger(max_conversations=2, max_per_conversation=3)
    ledger.record([f"f{i}.txt" for i in range(10)], user_email="u", conversation_id="c1")
    assert len(ledger.recent(user_email="u", conversation_id="c1", limit=99)) == 3
    ledger.record(["x"], user_email="u", conversation_id="c2")
    ledger.record(["y"], user_email="u", conversation_id="c3")
    # Oldest conversation evicted; the process-local ledger never grows without bound.
    assert ledger.recent(user_email="u", conversation_id="c1") == []
    assert ledger.recent(user_email="u", conversation_id="c3")


def test_just_written_files_reach_the_prompt_without_retrieval():
    ledger = ArtifactLedger()
    ledger.record(["index.html"], user_email="u", conversation_id="c", run_id="r1")
    # Retrieval knows nothing yet — the artifact section still carries the file.
    assembler = ContextAssembler(
        hybrid_search=lambda q, **kw: {"matches": []},
        recent_artifacts=ledger.recent,
    )
    assembled = assembler.assemble("방금 만든 파일에 다크모드 넣어줘", user_email="u", conversation_id="c")
    assert "index.html" in assembled.text
    names = [section["name"] for section in assembled.trace()["sections"]]
    assert "Files created in this conversation" in names


def test_no_ledger_means_no_section_not_an_empty_promise():
    assembler = ContextAssembler(hybrid_search=lambda q, **kw: {"matches": []})
    assembled = assembler.assemble("질문")
    assert "Files created in this conversation" not in assembled.text


# ── 2. critic semantic verification ──────────────────────────────────────────


def test_requirement_coverage_flags_a_declared_file_that_was_never_written():
    coverage = requirement_coverage(
        "todo 앱 html css js로 만들어줘",
        [_write_step("index.html")],
        FILE_ACTIONS,
    )
    assert "index.html" in coverage["files"]["written"]
    assert coverage["missing_files"]
    assert coverage["complete"] is False


def test_requirement_coverage_is_complete_when_every_declared_file_exists():
    coverage = requirement_coverage(
        "todo 앱 html css js로 만들어줘",
        [_write_step("index.html"), _write_step("style.css"), _write_step("app.js")],
        FILE_ACTIONS,
    )
    assert coverage["missing_files"] == []
    assert coverage["complete"] is True


def test_requests_without_a_declared_file_set_are_never_blocked():
    # No manifest → nothing deterministic to enforce; the critic still decides.
    coverage = requirement_coverage("이 코드 설명해줘", [], FILE_ACTIONS)
    assert coverage["files"]["declared"] == []
    assert coverage["complete"] is True


def test_explicit_requirement_lines_are_listed_but_never_block():
    coverage = requirement_coverage(
        "대시보드 만들어줘\n- 다크모드\n- 검색 기능\n1. CSV 내보내기",
        [],
        FILE_ACTIONS,
    )
    assert coverage["requirements"] == ["다크모드", "검색 기능", "CSV 내보내기"]
    # Feature-level matching is a judgement call, so it stays advisory.
    assert coverage["complete"] is True


def test_missing_files_become_an_honest_explanation_code():
    result = explain_run(
        state="NEEDS_REVIEW",
        loop={"parse_errors": 0, "repairs": {}},
        transcript=[
            {"state": "VERIFYING", "verdict": "PASS", "evidence": True},
            {
                "state": "VERIFYING",
                "requirement_coverage": {"missing_files": ["style.css", "app.js"]},
            },
        ],
    )
    assert result["code"] == "missing_files"
    assert result["ok"] is False
    joined = " ".join(detail["ko"] for detail in result["details"])
    assert "style.css" in joined


# ── 3. failure learning loop ─────────────────────────────────────────────────


def test_failed_runs_produce_a_concrete_next_step():
    missing = explain_run(
        state="NEEDS_REVIEW",
        loop={},
        transcript=[{
            "state": "VERIFYING",
            "requirement_coverage": {"missing_files": ["style.css"]},
        }],
    )
    assert "style.css" in missing["next_step"]["ko"]

    blocked = explain_run(
        state="FAILED",
        loop={},
        transcript=[{
            "state": "EXECUTING",
            "action": "run_command",
            "error": "BLOCKED: destructive action 'run_command' not permitted in agent mode.",
        }],
    )
    assert "run_command" in blocked["next_step"]["ko"]


def test_a_clean_successful_run_needs_no_next_step():
    result = explain_run(
        state="DONE",
        loop={},
        transcript=[{"state": "VERIFYING", "verdict": "PASS", "evidence": True}],
    )
    assert result["next_step"] is None


def test_a_strained_success_still_suggests_a_bigger_model():
    result = explain_run(
        state="DONE",
        loop={"parse_errors": 4, "corrections": 3, "repairs": {"fence": 5}},
        transcript=[{"state": "VERIFYING", "verdict": "PASS", "evidence": True}],
    )
    assert result["model_strain"]["level"] == "heavy"
    assert "모델" in result["next_step"]["ko"]


def test_the_next_run_in_a_project_reads_the_last_failure_diagnosis(tmp_path):
    store = ProjectSessionStore(tmp_path / "projects")
    project = store.create(title="대시보드", user_email="u@x.com")
    failed = explain_run(
        state="NEEDS_REVIEW",
        loop={},
        transcript=[{
            "state": "VERIFYING",
            "requirement_coverage": {"missing_files": ["style.css"]},
        }],
    )
    store.record_run(
        project["id"], run_id="r1", status="failed", final_state="NEEDS_REVIEW",
        files=["index.html"], explanation=failed, user_email="u@x.com",
    )
    summary = store.summary(project["id"], user_email="u@x.com")
    assert "style.css" in summary
    assert "do differently" in summary


def test_a_successful_run_does_not_carry_a_correction_into_the_next_plan(tmp_path):
    store = ProjectSessionStore(tmp_path / "projects")
    project = store.create(title="대시보드", user_email="u@x.com")
    store.record_run(
        project["id"], run_id="r1", status="ok", final_state="DONE",
        files=["index.html"],
        explanation=explain_run(
            state="DONE",
            loop={"parse_errors": 3, "repairs": {"fence": 4}},
            transcript=[{"state": "VERIFYING", "verdict": "PASS", "evidence": True}],
        ),
        user_email="u@x.com",
    )
    summary = store.summary(project["id"], user_email="u@x.com")
    assert "index.html" in summary
    assert "do differently" not in summary
