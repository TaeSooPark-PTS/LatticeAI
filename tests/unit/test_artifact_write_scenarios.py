"""FG harness — the file-generation scenario matrix (FG-01..FG-08).

Any model, any entry path, one guarantee: a file-creation request ends with a
structurally valid file on disk, and how-to questions never trigger writes.
FG-06 exercises the ArtifactWritePipeline seam: the agent JSON loop's raw
``write_file`` args.content passes the same extract→validate→repair pipeline
as the direct chat path before it can touch the disk.
"""

import asyncio
from pathlib import Path

from latticeai.api.chat_helpers import file_action_target, is_file_action_request
from latticeai.api.chat_intents import ingest_generated_enabled, next_available_path
from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
    filter_learnings,
    normalize_plan,
)
from latticeai.core.file_generation import (
    generate_file_content,
    infer_file_target,
    sanitize_write_content,
    validate_file_content,
)
from latticeai.services.tool_dispatch import collect_artifacts
from latticeai.tools import filesystem

# ── FG-01: explicit filename wins ───────────────────────────────────────

def test_fg01_explicit_html_filename_is_the_target():
    message = "hello.html 만들어줘"
    assert is_file_action_request(message)
    assert file_action_target(message) == "hello.html"


# ── FG-02: type keyword infers a filename ───────────────────────────────

def test_fg02_type_keyword_infers_target():
    message = "html 파일 만들어줘"
    assert is_file_action_request(message)
    assert file_action_target(message) is None
    assert infer_file_target(message) == "generated_page.html"


# ── FG-03: weak-model noise is stripped ─────────────────────────────────

def test_fg03_fenced_chatty_reply_yields_valid_file():
    async def dirty_model(context):
        return (
            "<think>plan the page</think>\n"
            "Sure! Here is your page:\n"
            "```html\n<!DOCTYPE html><html><head><title>x</title></head>"
            "<body>hi</body></html>\n```\n"
            "Let me know if you need anything else!"
        )

    content, meta = asyncio.run(
        generate_file_content(dirty_model, target_path="page.html", user_request="page")
    )
    assert content.startswith("<!DOCTYPE html>")
    assert "```" not in content and "Sure!" not in content
    assert meta["repaired"] is False


# ── FG-04: truncated html is repaired to a closed document ──────────────

def test_fg04_truncated_html_repaired_and_validates():
    truncated = "<!DOCTYPE html><html><head><title>t</title></head><body><p>hi</p>"
    fixed, meta = sanitize_write_content("page.html", truncated)
    assert meta["sanitized"] and meta["repaired"]
    ok, reason = validate_file_content(fixed, "page.html")
    assert ok, reason


# ── FG-05: json target parses ───────────────────────────────────────────

def test_fg05_json_from_prose_parses():
    async def model(context):
        return 'The data you asked for:\n```json\n{"users": [1, 2, 3]}\n```'

    content, _ = asyncio.run(
        generate_file_content(model, target_path="data.json", user_request="data")
    )
    ok, reason = validate_file_content(content, "data.json")
    assert ok, reason


# ── FG-06: agent write_file path is sanitized before disk ───────────────

def _deps_for_dispatch(written):
    async def generate_as(*a, **k):  # pragma: no cover — not used here
        return "{}"

    async def generate(*a, **k):  # pragma: no cover
        return "{}"

    def execute_tool(name, args):
        written.append(dict(args))
        return {"success": True, "path": args.get("path", ""), "bytes": len(args.get("content", ""))}

    return AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: {
            "risk": "write", "destructive": False, "shell": False,
            "network": False, "auto_approve": True, "sandbox": "workspace",
            "rollback": "git",
        },
        risk_level=lambda p: "medium",
        check_role=lambda name, user: None,
        tool_governance={"write_file": {"auto_approve": True}},
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **k: "",
        clear_history=lambda keep_last: {},
        knowledge_save=lambda *a, **k: None,
        audit=lambda *a, **k: None,
        planner_prompt="PLAN", executor_prompt="EXEC",
        critic_prompt="CRIT", memory_updater_prompt="MEM",
        agent_root=Path("/tmp"),
    )


def test_fg06_agent_dispatch_strips_fences_from_write_file_content():
    written = []
    runtime = SingleAgentRuntime(_deps_for_dispatch(written))
    ctx = AgentRunContext()
    ctx.plan = {"goal": "make an html page"}
    dirty = (
        "Sure! Here you go:\n```html\n<!DOCTYPE html><html><head>"
        "<title>t</title></head><body>ok</body></html>\n```"
    )
    runtime._dispatch_step(
        ctx, "write_file", "writing", {"path": "page.html", "content": dirty},
        {"risk": "write", "auto_approve": True, "destructive": False,
         "shell": False, "network": False, "sandbox": "workspace", "rollback": "git"},
        "medium", "tester",
    )
    assert len(written) == 1
    saved = written[0]["content"]
    assert saved.startswith("<!DOCTYPE html>") and "```" not in saved
    step = ctx.transcript[-1]
    assert step["content_sanitize"]["sanitized"] is True
    assert step["content_sanitize"]["repaired"] is False


def test_fg06b_agent_dispatch_leaves_clean_content_untouched():
    written = []
    runtime = SingleAgentRuntime(_deps_for_dispatch(written))
    ctx = AgentRunContext()
    clean = "<!DOCTYPE html><html><head><title>t</title></head><body>ok</body></html>"
    runtime._dispatch_step(
        ctx, "write_file", "writing", {"path": "page.html", "content": clean},
        {"risk": "write", "auto_approve": True, "destructive": False,
         "shell": False, "network": False, "sandbox": "workspace", "rollback": "git"},
        "medium", "tester",
    )
    assert written[0]["content"] == clean
    assert "content_sanitize" not in ctx.transcript[-1]


def test_fg06c_artifacts_carry_repaired_flag_from_transcript():
    transcript = [
        {
            "state": AgentState.EXECUTING.value,
            "action": "write_file",
            "result": {"path": "page.html", "bytes": 120},
            "content_sanitize": {"sanitized": True, "repaired": True, "reason": "truncated"},
        },
    ]
    artifacts = collect_artifacts(transcript)
    assert artifacts == [{
        "kind": "file", "path": "page.html", "filename": "page.html",
        "bytes": 120, "previewable": True, "valid": True, "repaired": True,
    }]


# ── FG-07: how-to questions stay in chat ────────────────────────────────

def test_fg07_howto_questions_do_not_route_to_file_tools():
    for message in (
        "html 파일 만드는 방법 알려줘",
        "how do I create an html file?",
        "파이썬으로 파일 저장하는 예시 보여줘",
    ):
        assert not is_file_action_request(message), message


# ── FG-08: multi-file project scaffold stays valid ──────────────────────

def test_fg08_web_project_scaffold_files_validate(tmp_path, monkeypatch):
    import latticeai.tools as tools

    monkeypatch.setattr(tools, "AGENT_ROOT", tmp_path)
    result = filesystem.create_web_project("todo-app")
    assert result["file_count"] >= 3
    index = tmp_path / "todo-app" / "index.html"
    ok, reason = validate_file_content(index.read_text(encoding="utf-8"), "index.html")
    assert ok, reason
    package = tmp_path / "todo-app" / "package.json"
    ok, reason = validate_file_content(package.read_text(encoding="utf-8"), "package.json")
    assert ok, reason


# ── supporting loop-quality contracts ───────────────────────────────────

def test_empty_content_is_never_repaired():
    content, meta = sanitize_write_content("src/__init__.py", "")
    assert content == "" and meta["sanitized"] is False


def test_normalize_plan_synthesizes_file_step_for_file_intent():
    plan, fixes = normalize_plan({"action": "plan", "steps": []}, "html 파일 만들어줘")
    assert plan["steps"][0]["action"] == "write_file"
    assert plan["steps"][0]["args"]["path"] == "generated_page.html"
    assert "heuristic_file_step" in fixes and "goal_defaulted" in fixes


def test_normalize_plan_filters_junk_steps_and_clamps_estimates():
    plan, fixes = normalize_plan(
        {"goal": "g", "steps": ["junk", {"noaction": 1}, {"action": "read_file"}],
         "estimated_steps": "many"},
        "whatever",
    )
    assert plan["steps"] == [{"action": "read_file"}]
    assert plan["estimated_steps"] == 1
    assert "steps_filtered" in fixes and "estimated_steps_invalid" in fixes


def test_normalize_plan_keeps_valid_plans_unchanged():
    original = {
        "goal": "do it", "steps": [{"action": "read_file", "args": {"path": "a"}}],
        "estimated_steps": 2, "requires_approval": False, "rollback_strategy": "git",
    }
    plan, fixes = normalize_plan(dict(original), "do it")
    assert plan["goal"] == "do it" and plan["steps"] == original["steps"]
    assert fixes == []


def test_filter_learnings_drops_trivial_and_duplicate_entries():
    kept = filter_learnings([
        "파일을 만들었습니다",
        "Task completed successfully",
        "짧음",
        "MLX 모델은 4bit 양자화에서 메모리를 절반만 사용한다",
        "MLX 모델은 4bit 양자화에서 메모리를 절반만 사용한다",
    ])
    assert kept == ["MLX 모델은 4bit 양자화에서 메모리를 절반만 사용한다"]


def test_next_available_path_suffixes_existing_targets(tmp_path):
    assert next_available_path(tmp_path, "page.html") == "page.html"
    (tmp_path / "page.html").write_text("x", encoding="utf-8")
    assert next_available_path(tmp_path, "page.html") == "page_2.html"
    (tmp_path / "page_2.html").write_text("x", encoding="utf-8")
    assert next_available_path(tmp_path, "page.html") == "page_3.html"
    # regenerating "page_2.html" itself also lands on the first free slot
    assert next_available_path(tmp_path, "page_2.html") == "page_3.html"


def test_ingest_generated_toggle_reads_env(monkeypatch):
    monkeypatch.delenv("LATTICEAI_INGEST_GENERATED", raising=False)
    assert ingest_generated_enabled() is True
    monkeypatch.setenv("LATTICEAI_INGEST_GENERATED", "0")
    assert ingest_generated_enabled() is False
