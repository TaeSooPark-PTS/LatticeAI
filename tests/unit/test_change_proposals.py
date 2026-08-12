"""Change proposal tests (v9.6.0).

Covers the central change-class governor, proposal staging (diff, tier,
exact staged content), approve-applies / reject-discards semantics, and the
agent loop integration: additive creates run with minimal friction while
mutations of existing files become review proposals instead of writes.
"""

import asyncio
import hashlib

from latticeai.core.agent import (
    AgentDeps,
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
)
from latticeai.core.tool_governor import classify_tool_call
from latticeai.services.change_proposals import ChangeProposalService


class FakeReviewQueue:
    def __init__(self):
        self.items = {}
        self.counter = 0

    def create(self, **kwargs):
        self.counter += 1
        item = {"id": f"rp-{self.counter}", "status": "pending", **kwargs}
        self.items[item["id"]] = item
        return item

    def list(self, **kwargs):
        source = kwargs.get("source")
        status = kwargs.get("status")
        return {
            "items": [
                it for it in self.items.values()
                if (source is None or it.get("source") == source)
                and (status is None or it.get("status") == status)
            ]
        }

    def get(self, item_id, *, workspace_id=None):
        if item_id not in self.items:
            raise FileNotFoundError(item_id)
        return self.items[item_id]

    def approve(self, item_id, *, workspace_id=None):
        self.items[item_id]["status"] = "approved"
        return self.items[item_id]

    def dismiss(self, item_id, *, workspace_id=None):
        self.items[item_id]["status"] = "dismissed"
        return self.items[item_id]


def _service(tmp_path):
    queue = FakeReviewQueue()

    def resolve(path=""):
        candidate = (tmp_path / path).resolve()
        assert str(candidate).startswith(str(tmp_path))
        return candidate

    return ChangeProposalService(review_queue=queue, resolve_path=resolve), queue


def _stage_delete(queue, path: str, base_sha256: str):
    """Stage a ``file_delete`` proposal the way a client does.

    There is no Python-side factory for delete proposals: they are created
    through ``POST /automation/reviews`` (the frontend's proposal-rebase flow
    is the live producer), so the tests stage them at that same seam.
    """
    return queue.create(
        title=f"파일 삭제 제안: {path}",
        summary="",
        source="change_proposal",
        kind="file_delete",
        payload={
            "path": path,
            "tier": "large",
            "base_exists": True,
            "base_sha256": base_sha256,
        },
    )


# ── governor classification ─────────────────────────────────────────────

def test_classify_read_tools():
    verdict = classify_tool_call("read_file", {}, policy={"risk": "read"})
    assert verdict["change_class"] == "read"
    assert verdict["proposal_required"] is False


def test_classify_new_file_is_additive():
    verdict = classify_tool_call(
        "write_file", {"path": "new.txt"},
        policy={"risk": "write"}, path_exists=lambda p: False,
    )
    assert verdict["change_class"] == "additive"
    assert verdict["proposal_required"] is False


def test_classify_overwrite_is_mutation():
    verdict = classify_tool_call(
        "write_file", {"path": "exists.txt"},
        policy={"risk": "write"}, path_exists=lambda p: True,
    )
    assert verdict["change_class"] == "mutation"
    assert verdict["proposal_required"] is True


def test_classify_edit_and_delete():
    assert classify_tool_call("edit_file", {"path": "a"}, policy={"risk": "write"})["change_class"] == "mutation"
    assert classify_tool_call("delete_file", {"path": "a"}, policy={})["change_class"] == "destructive"
    assert classify_tool_call("run_command", {}, policy={"risk": "exec"})["change_class"] == "exec"
    assert classify_tool_call("knowledge_save", {}, policy={"risk": "write"})["change_class"] == "additive"


# ── proposal staging ────────────────────────────────────────────────────

def test_overwrite_becomes_proposal_with_diff_and_tier(tmp_path):
    (tmp_path / "note.txt").write_text("old line\n", encoding="utf-8")
    service, queue = _service(tmp_path)
    verdict = service.review(
        "write_file", {"path": "note.txt", "content": "new line\n"},
        policy={"risk": "write"}, user_email="a@b.c",
    )
    assert verdict["decision"] == "proposed"
    item = verdict["proposal"]
    assert item["source"] == "change_proposal"
    assert item["kind"] == "file_update"
    payload = item["payload"]
    assert payload["tier"] == "small"
    assert any(line.startswith("-old line") for line in payload["diff"])
    assert any(line.startswith("+new line") for line in payload["diff"])
    # nothing applied yet
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "old line\n"


def test_new_file_is_allowed_without_proposal(tmp_path):
    service, queue = _service(tmp_path)
    verdict = service.review(
        "write_file", {"path": "fresh.txt", "content": "hi"}, policy={"risk": "write"},
    )
    assert verdict["decision"] == "allow_additive"
    assert queue.items == {}


def test_edit_file_stages_exact_result(tmp_path):
    (tmp_path / "doc.md").write_text("hello world\n", encoding="utf-8")
    service, queue = _service(tmp_path)
    verdict = service.review(
        "edit_file",
        {"path": "doc.md", "old_string": "world", "new_string": "brain"},
        policy={"risk": "write"},
    )
    assert verdict["decision"] == "proposed"
    assert verdict["proposal"]["payload"]["new_content"] == "hello brain\n"


def test_edit_file_without_match_falls_through(tmp_path):
    (tmp_path / "doc.md").write_text("hello\n", encoding="utf-8")
    service, queue = _service(tmp_path)
    verdict = service.review(
        "edit_file",
        {"path": "doc.md", "old_string": "absent", "new_string": "x"},
        policy={"risk": "write"},
    )
    assert verdict is None
    assert queue.items == {}


def test_non_file_tools_fall_through(tmp_path):
    service, _ = _service(tmp_path)
    assert service.review("run_command", {"command": "ls"}, policy={"risk": "exec"}) is None


# ── approve applies / reject discards ───────────────────────────────────

def test_approve_applies_exactly_reviewed_content(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, queue = _service(tmp_path)
    verdict = service.review(
        "write_file", {"path": "site.html", "content": "<new>"}, policy={"risk": "write"},
    )
    item_id = verdict["proposal"]["id"]
    result = service.approve_and_apply(item_id)
    assert result["applied"] is True
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<new>"
    assert queue.items[item_id]["status"] == "approved"


def test_reject_discards_without_touching_disk(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, queue = _service(tmp_path)
    verdict = service.review(
        "write_file", {"path": "site.html", "content": "<new>"}, policy={"risk": "write"},
    )
    item_id = verdict["proposal"]["id"]
    result = service.reject(item_id)
    assert result["applied"] is False
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<old>"
    assert queue.items[item_id]["status"] == "dismissed"


def test_delete_proposal_applies_on_approve(tmp_path):
    (tmp_path / "gone.txt").write_text("bye", encoding="utf-8")
    service, queue = _service(tmp_path)
    item = _stage_delete(
        queue, "gone.txt", hashlib.sha256(b"bye").hexdigest()
    )
    assert (tmp_path / "gone.txt").exists()
    service.approve_and_apply(item["id"])
    assert not (tmp_path / "gone.txt").exists()


def test_pending_lists_only_change_proposals(tmp_path):
    service, queue = _service(tmp_path)
    queue.create(title="other", source="workflow_run", kind="suggestion", payload={})
    service.propose_file_update(path="a.txt", new_content="x")
    pending = service.pending()
    assert pending["count"] == 1
    assert pending["items"][0]["kind"] == "file_update"
    assert pending["contract"]["mutations"] == "proposal"


# ── agent loop integration ──────────────────────────────────────────────

class _Req:
    message = "update the file"
    conversation_id = None
    temperature = 0.2
    workspace_id = None
    source = "test"


def _agent_deps(replies, tmp_path, service):
    queue_replies = list(replies)

    async def generate_as(model_id, message, context, max_tokens, temperature):
        return queue_replies.pop(0)

    async def generate(**kwargs):
        return '{"action": "noop"}'

    writes = []

    def execute_tool(name, args):
        writes.append((name, args))
        target = tmp_path / args.get("path", "out.txt")
        target.write_text(str(args.get("content") or ""), encoding="utf-8")
        return {"ok": True, "path": args.get("path", "")}

    write_policy = {
        "auto_approve": False, "risk": "write", "shell": False, "network": False,
        "destructive": False, "sandbox": "workspace", "rollback": "git",
    }
    deps = AgentDeps(
        generate_as=generate_as,
        generate=generate,
        execute_tool=execute_tool,
        policy_for=lambda name, args: dict(write_policy),
        risk_level=lambda p: p["risk"],
        check_role=lambda name, user: None,
        tool_governance={"write_file": dict(write_policy)},
        file_create_actions=frozenset({"write_file"}),
        recent_chat_context=lambda **kw: "",
        clear_history=lambda keep: {"ok": True},
        knowledge_save=lambda *a, **kw: None,
        audit=lambda *a, **kw: None,
        planner_prompt="p", executor_prompt="e", critic_prompt="c",
        memory_updater_prompt="m", agent_root=tmp_path,
        change_governor=service,
    )
    return deps, writes


def _run_agent(deps):
    runtime = SingleAgentRuntime(deps)
    ctx = AgentRunContext()
    ctx.state = AgentState.PLANNING
    req = _Req()

    async def run():
        await runtime.plan(ctx, req, "en", "u@t")
        runtime.approve(ctx, "u@t")
        if ctx.state == AgentState.EXECUTING:
            await runtime.run_to_completion(ctx, req, "en", "u@t", max_steps=5, max_retry=1)

    asyncio.run(run())
    return ctx


def test_agent_new_file_write_executes_without_approval_block(tmp_path):
    service, queue = _service(tmp_path)
    deps, writes = _agent_deps([
        '{"action": "plan", "goal": "write", "steps": [{"action": "write_file"}]}',
        '{"action": "write_file", "args": {"path": "fresh.txt", "content": "hi"}}',
        '{"action": "final", "message": "done"}',
        '{"action": "verdict", "verdict": "PASS", "next_state": "DONE"}',
    ], tmp_path, service)
    ctx = _run_agent(deps)
    assert ctx.state == AgentState.DONE
    assert writes  # the additive write actually executed
    assert queue.items == {}  # no proposal needed
    assert ctx.trace.summary()["tool_outcomes"] == {"ok": 1}


def test_agent_overwrite_becomes_proposal_not_write(tmp_path):
    (tmp_path / "site.html").write_text("<old>", encoding="utf-8")
    service, queue = _service(tmp_path)
    deps, writes = _agent_deps([
        '{"action": "plan", "goal": "update", "steps": [{"action": "write_file"}]}',
        '{"action": "write_file", "args": {"path": "site.html", "content": "<new>"}}',
        '{"action": "final", "message": "done"}',
        '{"action": "verdict", "verdict": "PASS", "next_state": "DONE"}',
    ], tmp_path, service)
    ctx = _run_agent(deps)
    assert ctx.state == AgentState.DONE
    assert writes == []  # nothing written directly
    assert len(queue.items) == 1  # staged as a proposal instead
    assert (tmp_path / "site.html").read_text(encoding="utf-8") == "<old>"
    assert ctx.trace.summary()["tool_outcomes"] == {"proposed": 1}
    proposed_steps = [
        s for s in ctx.transcript
        if isinstance(s.get("result"), dict) and s["result"].get("proposed")
    ]
    assert proposed_steps and proposed_steps[0]["result"]["proposal_id"]
