#!/usr/bin/env python3
"""Build the committed Python↔Rust **agent loop** parity fixtures (v11.5.1).

``rust/lattice-agent`` now owns the PLAN → EXECUTE → VERIFY → ROLLBACK
orchestration that ``latticeai.core.agent`` has always owned, with the Python
worker behind three seam endpoints. A port of a *state machine* is only worth
having if something keeps proving it still reaches the same states, by the same
route, with the same record — so this script is the Python half of that proof.

It runs the **real** functions, never a re-description of them:

* the deterministic helpers — ``extract_action_details``, ``normalize_plan``,
  ``infer_file_target`` / ``infer_project_manifest``, ``requirement_coverage``,
  ``artifact_checklist``, ``files_written``, ``compact_transcript``,
  ``_truncate_strings``, ``filter_learnings``, ``PhaseBudgets`` /
  ``TranscriptBudget``;
* the verification verdict mapping, by calling the real ``verify()`` over a
  verdict × evidence × coverage × retry grid with a scripted critic;
* the run store's ``serialize_run_context`` / ``restore_run_context``;
* and **end-to-end trajectories**: the real :class:`SingleAgentRuntime`, driven
  by a scripted LLM and the real tool registry inside a throwaway
  ``AGENT_ROOT``, for seven scenarios that between them exercise every branch
  the loop can take to a terminal state.

Two consumers read what it writes:

* ``tests/unit/test_agent_loop_parity_contract.py`` re-runs the Python loop over
  the same scripts and asserts the committed goldens still hold — so a change to
  a Python gate fails loudly instead of silently invalidating the contract the
  Rust side is pinned to;
* ``rust/lattice-agent/tests/agent_loop.rs`` drives the native loop against a
  fake worker replaying the recorded completions and tool results, and asserts
  the same trajectories.

Determinism is the design constraint. Four normalisation rules make the record
machine-independent, and both sides apply them:

1. the absolute workspace root becomes ``<AGENT_ROOT>``;
2. ``at`` keys are dropped (trace timestamps);
3. ``stderr`` keys are dropped (git's text is version- and locale-specific);
4. a JSON decoder detail is collapsed to ``<decoder-detail>`` — CPython renamed
   several of those messages in 3.14, and pinning a Python patch release is not
   what this contract is for.

Usage::

    .venv/bin/python scripts/generate_agent_loop_fixtures.py
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The tool registry resolves AGENT_ROOT at import; point it somewhere harmless
# before anything imports it, then patch it per run through `use_workspace`.
os.environ.setdefault(
    "LATTICEAI_AGENT_ROOT", str(Path(tempfile.gettempdir()) / "agent-loop-fixtures-import")
)

import latticeai.services.tool_dispatch as tool_dispatch  # noqa: E402
import latticeai.tools as tools  # noqa: E402
from latticeai.api.chat_contracts import AgentRequest  # noqa: E402
from latticeai.core.agent import (  # noqa: E402
    AgentRunContext,
    AgentState,
    SingleAgentRuntime,
)
from latticeai.core.agent.deps import AgentDeps  # noqa: E402
from latticeai.core.agent_helpers import (  # noqa: E402
    PhaseBudgets,
    TranscriptBudget,
    _truncate_strings,
    artifact_checklist,
    compact_transcript,
    extract_action_details,
    files_written,
    filter_learnings,
    normalize_plan,
    requirement_coverage,
)
from latticeai.core.agent_profiles import (  # noqa: E402
    COMPACT_MAX_PARAMS_B,
    model_size_b,
    profile_for_model,
)
from latticeai.core.agent_trace import LoopTrace  # noqa: E402
from latticeai.core.file_generation import (  # noqa: E402
    infer_file_target,
    infer_project_manifest,
    sanitize_write_content,
)
from latticeai.core.run_store import (  # noqa: E402
    restore_run_context,
    serialize_run_context,
)
from latticeai.core.tool_registry import (  # noqa: E402
    FILE_CREATE_ACTIONS,
    LOCAL_WRITE_BLOCKED_PREFIXES,
    SCOPED_KNOWLEDGE_TOOLS,
    TOOL_GOVERNANCE,
    TOOL_GOVERNANCE_DEFAULT,
)
from latticeai.tools.documents import document_output_target  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "rust" / "fixtures" / "agent_loop"
GOLDEN_DIR = FIXTURE_DIR / "golden"
SCHEMA = "agent-loop-parity/v1"

DECODER_DETAIL_PREFIX = "Agent did not return valid JSON: "
ROOT_PLACEHOLDER = "<AGENT_ROOT>"


# ── normalisation ─────────────────────────────────────────────────────────────
def normalize(value: Any, root: Path) -> Any:
    """Apply the four machine-independence rules, recursively."""
    if isinstance(value, str):
        text = value.replace(str(root), ROOT_PLACEHOLDER)
        if text.startswith(DECODER_DETAIL_PREFIX):
            return f"{DECODER_DETAIL_PREFIX}<decoder-detail>"
        return text
    if isinstance(value, dict):
        return {
            key: normalize(item, root)
            for key, item in value.items()
            if key not in ("at", "stderr")
        }
    if isinstance(value, list):
        return [normalize(item, root) for item in value]
    return value


@contextmanager
def use_workspace(root: Path) -> Iterator[Path]:
    """Point the real tool registry at ``root`` for the duration.

    Two modules hold the constant: ``latticeai.tools`` defines it and
    ``latticeai.services.tool_dispatch`` imported the value, so patching one
    would leave the snapshot/rollback ports reading the real workspace.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    resolved = root.resolve()
    previous = (tools.AGENT_ROOT, tool_dispatch.AGENT_ROOT)
    tools.AGENT_ROOT = resolved
    tool_dispatch.AGENT_ROOT = resolved
    try:
        yield resolved
    finally:
        tools.AGENT_ROOT, tool_dispatch.AGENT_ROOT = previous


# ── the deterministic helper grids ────────────────────────────────────────────
#: Raw model outputs chosen for the rung of the tolerance chain each one reaches.
RAW_ACTIONS: Dict[str, str] = {
    "clean": '{"action": "final", "message": "done"}',
    "clean_with_args": '{"thoughts": "t", "action": "write_file", "args": {"path": "a.md"}}',
    "fence_json": '```json\n{"action": "read_file", "args": {"path": "a.md"}}\n```',
    "fence_bare": '```\n{"action": "final"}\n```',
    "fence_with_prose": 'Sure!\n```json\n{"action": "final"}\n```\nHope that helps.',
    "fence_multiline": '```json\n{\n  "action": "final",\n  "message": "ok"\n}\n```',
    "think_then_json": '<think>hmm {"action": "wrong"}</think>\n{"action": "right"}',
    "thinking_tag": '<thinking>plan</thinking>{"action": "final"}',
    "reasoning_tag": '<reasoning>why</reasoning>\n{"action": "final"}',
    "think_uppercase": '<THINK>x</THINK>{"action": "final"}',
    "think_unclosed": '<think>never closed {"action": "final"}',
    "think_mismatched": '<think>{"action": "a"}</reasoning>',
    "slice_prefix": 'I will call: {"action": "write_file"}',
    "slice_suffix": '{"action": "write_file"} — that is the call.',
    "slice_both": 'Calling {"action": "final", "message": "완료"} now.',
    "slice_nested": 'note {"action": "a", "args": {"b": {"c": 1}}} end',
    "trailing_comma_object": '{"action": "final", "message": "x",}',
    "trailing_comma_array": '{"action": "a", "args": {"items": [1, 2,]}}',
    "trailing_comma_nested": '{"action": "a", "args": {"x": 1,},}',
    "python_literal": "{'action': 'write_file', 'args': {'path': 'a.md'}}",
    "python_literal_true": "{'action': 'a', 'ok': True, 'bad': False, 'none': None}",
    "python_literal_trailing": "{'action': 'final', 'message': 'hi',}",
    "python_literal_nested": "{'action': 'a', 'args': {'items': [1, 2.5, 'x']}}",
    "python_literal_escapes": "{'action': 'a', 'note': 'line\\nbreak'}",
    "python_literal_not_dict": "('a', 'b')",
    "broken_prose": "I think we should start by reading the notes.",
    "broken_empty": "",
    "broken_whitespace": "   \n  ",
    "broken_unclosed": '{"action": "final"',
    "broken_missing_value": '{"action": }',
    "broken_unquoted_key": "{action: 1}",
    "no_action_key": '{"thoughts": "no action here"}',
    "not_an_object": "[1, 2, 3]",
    "bare_number": "42",
    "bare_string": '"just text"',
    "korean_prose_slice": '작업 계획: {"action": "final", "message": "완료"} 끝.',
    "double_object": '{"action": "a"} {"action": "b"}',
    "unicode_thoughts": '{"action": "final", "thoughts": "가나다라마바사"}',
}

#: Plans chosen for the seven normalisation rules and their interactions.
PLAN_CASES: Dict[str, Dict[str, Any]] = {
    "complete": {
        "plan": {"goal": "g", "steps": [{"action": "read_file", "args": {"path": "a.md"}}],
                 "estimated_steps": 1, "requires_approval": False, "rollback_strategy": "none"},
        "message": "read a.md",
    },
    "not_an_object": {"plan": ["nope"], "message": "hi"},
    "null_plan": {"plan": None, "message": "hi"},
    "string_plan": {"plan": "a plan", "message": "hi"},
    "blank_goal": {"plan": {"goal": "   ", "steps": []}, "message": "do the thing"},
    "missing_goal": {"plan": {"steps": []}, "message": "do the thing"},
    "numeric_goal": {"plan": {"goal": 5, "steps": []}, "message": "hi"},
    "junk_steps": {
        "plan": {"goal": "g", "steps": ["x", {"no_action": 1}, {"action": ""},
                                        {"action": "read_file"}]},
        "message": "g",
    },
    "steps_not_a_list": {"plan": {"goal": "g", "steps": "read a file"}, "message": "g"},
    "empty_steps": {"plan": {"goal": "g", "steps": []}, "message": "g"},
    "manifest_empty_plan": {"plan": {"goal": "g", "steps": []},
                            "message": "todo 앱 html css js 만들어줘"},
    "manifest_partial": {
        "plan": {"goal": "g", "steps": [{"action": "write_file", "args": {"path": "index.html"}}]},
        "message": "todo 앱 html css js 만들어줘",
    },
    "manifest_covered": {
        "plan": {"goal": "g", "steps": [
            {"action": "write_file", "args": {"path": "page.HTML"}},
            {"action": "write_file", "args": {"path": "a.css"}},
            {"action": "generate_file", "args": {"path": "b.js"}}]},
        "message": "todo 앱 html css js 만들어줘",
    },
    "manifest_with_read": {
        "plan": {"goal": "g", "steps": [{"action": "read_file", "args": {"path": "spec.md"}},
                                        {"action": "write_file", "args": {"path": "index.html"}}]},
        "message": "todo 앱 html css js 만들어줘",
    },
    "manifest_react": {"plan": {}, "message": "react 로 todo 앱 만들어줘"},
    "manifest_python": {"plan": {}, "message": "mytool 패키지 파이썬으로 만들어줘"},
    "heuristic_single_file": {"plan": {}, "message": "html 파일 만들어줘"},
    "heuristic_long_message": {"plan": {}, "message": "html 파일 만들어줘 " + "가" * 200},
    "estimated_string": {"plan": {"goal": "g", "estimated_steps": "4"}, "message": "g"},
    "estimated_float": {"plan": {"goal": "g", "estimated_steps": 3.7}, "message": "g"},
    "estimated_invalid": {"plan": {"goal": "g", "estimated_steps": "many"}, "message": "g"},
    "estimated_list": {"plan": {"goal": "g", "estimated_steps": [3]}, "message": "g"},
    "coerced_tail": {"plan": {"goal": "g", "requires_approval": "yes", "rollback_strategy": 7},
                     "message": "g"},
    "rollback_kept": {"plan": {"goal": "g", "rollback_strategy": "git"}, "message": "g"},
}

#: Requests the two inference functions are asked about.
INFERENCE_MESSAGES: List[str] = [
    "html 파일 만들어줘", "write me a python script", "csv 저장해줘", "html이 뭐야?",
    "만들어줘", "", "   ", "html과 css 만들어줘", "html and css 만들어줘",
    "todo 앱 html+css+js로 만들어줘", "웹페이지 js로 만들어줘", "웹페이지 css로 만들어줘",
    "react 로 todo 앱 만들어줘", "리액트로 만들어줘", "vite 앱 만들어줘",
    "mytool 패키지 파이썬으로 만들어줘", "my-tool 패키지 파이썬으로 생성",
    "파이썬 패키지 만들어줘", "index.html 이랑 style.css 만들어줘",
    "웹사이트 css js 만들어줘", "마크다운 파일 작성해줘", "yaml 만들어줘",
]

#: ``(tool_name, filename)`` pairs for the document-target resolver. Chosen for
#: the branches they reach, not for realism: a tool that is not a document
#: creator, each of the four that are, a name that already carries its suffix,
#: one that carries the wrong one, a path that must be reduced to its basename,
#: characters the sanitizer replaces, and the empty names that fall back to
#: ``artifact<suffix>``. The Rust port takes the basename with its own
#: ``path_name`` and has its own empty-name fallback, so those two are exactly
#: where the two implementations could disagree unnoticed.
DOCUMENT_TARGET_CASES: List[Dict[str, str]] = [
    {"tool": "write_file", "filename": "notes.md"},
    {"tool": "create_docx", "filename": "report.docx"},
    {"tool": "create_docx", "filename": "report"},
    {"tool": "create_docx", "filename": "report.pdf"},
    {"tool": "create_xlsx", "filename": "budget.xlsx"},
    {"tool": "create_pptx", "filename": "deck"},
    {"tool": "create_pdf", "filename": "invoice"},
    {"tool": "create_pdf", "filename": "sub/dir/invoice.pdf"},
    {"tool": "create_pdf", "filename": "../../escape.pdf"},
    {"tool": "create_docx", "filename": "회의 기록.docx"},
    {"tool": "create_docx", "filename": "a/b*c?d.docx"},
    {"tool": "create_docx", "filename": ""},
    {"tool": "create_pptx", "filename": "   "},
    {"tool": "create_xlsx", "filename": "REPORT.XLSX"},
    {"tool": "create_pdf", "filename": ".pdf"},
    {"tool": "unknown_tool", "filename": "x.docx"},
]

#: Model ids for the profile dial. The interesting ones are the quantization
#: suffixes (``4bit`` is not a parameter count), the multi-size ids where the
#: *smallest* wins, and the boundary at ``COMPACT_MAX_PARAMS_B`` itself.
PROFILE_MODEL_IDS: List[str] = [
    "mlx-community/gemma-4-12B-it-4bit",
    "qwen2.5-1.5b",
    "llama-3.2-3B",
    "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "phi-4-mini-3.8b-8bit",
    "some-model-4b",
    "some-model-4.0b",
    "some-model-4.1b",
    "gpt-4o",
    "claude-sonnet",
    "",
    "   ",
    "model-8bit",
    "abc123b",
    "7b-and-1.5b-mixed",
    "Model-70B-Instruct",
]

#: ``LATTICEAI_AGENT_PROFILE`` values, including the two that must fall through
#: to the size heuristic rather than failing the run.
PROFILE_OVERRIDES: List[str] = ["", "standard", "compact", "COMPACT", "nonsense"]

#: Transcripts the artifact/coverage helpers are asked about.
TRANSCRIPT_CASES: Dict[str, Dict[str, Any]] = {
    "empty": {"message": "todo 앱 html css js 만들어줘", "transcript": []},
    "one_write": {
        "message": "todo 앱 html css js 만들어줘",
        "transcript": [{"state": "EXECUTING", "action": "write_file",
                        "args": {"path": "index.html"},
                        "result": {"path": "index.html", "bytes": 10}}],
    },
    "all_written": {
        "message": "todo 앱 html css js 만들어줘",
        "transcript": [
            {"state": "EXECUTING", "action": "write_file", "args": {"path": "index.html"},
             "result": {"path": "index.html", "bytes": 10}},
            {"state": "EXECUTING", "action": "write_file", "args": {"path": "sub/STYLE.CSS"},
             "result": {"path": "sub/STYLE.CSS", "bytes": 3}},
            {"state": "EXECUTING", "action": "write_file", "args": {"path": "app.js"},
             "result": {"path": "app.js", "bytes": 3},
             "content_sanitize": {"sanitized": True, "repaired": True}},
        ],
    },
    "blocked_and_repeated": {
        "message": "make a note",
        "transcript": [
            {"state": "EXECUTING", "action": "write_file", "args": {"path": "a.md"},
             "error": "BLOCKED: nope"},
            {"state": "EXECUTING", "action": "write_file", "args": {"path": "a.md"},
             "result": {"path": "a.md", "bytes": 2}},
            {"state": "EXECUTING", "action": "write_file", "args": {"path": "a.md"},
             "result": {"path": "a.md", "bytes": 2}},
            {"state": "VERIFYING", "action": "write_file", "result": {"path": "ignored.md"}},
            {"state": "EXECUTING", "action": "read_file", "result": {"path": "skip.md"}},
        ],
    },
    "requirements_listed": {
        "message": "만들어줘:\n- 다크모드\n* dark mode\n1. 검색 기능\n2) 필터\nfree prose\n- ab",
        "transcript": [],
    },
    "requirements_capped": {
        "message": "\n".join(f"- item number {index}" for index in range(15)),
        "transcript": [],
    },
    "proposed_step": {
        "message": "make a note",
        "transcript": [{"state": "EXECUTING", "action": "write_file", "args": {"path": "a.md"},
                        "result": {"proposed": True, "proposal_id": "p1"}}],
    },
}

#: Values `_truncate_strings` is asked about, with the cap each one uses.
TRUNCATE_CASES: List[Dict[str, Any]] = [
    {"key": "short", "limit": 700, "value": {"a": "hello"}},
    {"key": "exact", "limit": 5, "value": "abcde"},
    {"key": "over", "limit": 5, "value": "abcdefgh"},
    {"key": "korean", "limit": 5, "value": "가" * 10},
    {"key": "nested", "limit": 3, "value": {"a": ["abcdef", {"b": "xyz!"}], "n": 5, "t": True}},
    {"key": "null_and_float", "limit": 2, "value": {"a": None, "b": 1.5, "c": []}},
]

LEARNING_CASES: List[List[Any]] = [
    ["short", "파일을 만들었습니다", "Successfully created the file",
     "Vite needs the entry script tag before </body> or the app never mounts",
     "VITE NEEDS THE ENTRY SCRIPT TAG BEFORE </BODY> OR THE APP NEVER MOUNTS", None],
    ["작업을 완료했습니다", "task was completed", "file was created",
     "Successfully created the file, but the CSS never loaded because the path was wrong"],
    [],
    [123456789012345, "  padded learning that is long enough  "],
]


#: A root that matches nothing, for grids that never carry a path.
NO_ROOT = Path("/__no_workspace_in_this_grid__")


def helper_rows() -> Dict[str, Any]:
    """Every deterministic helper, over its grid."""
    actions = []
    for key, raw in RAW_ACTIONS.items():
        try:
            action, repairs = extract_action_details(raw)
            actions.append({"key": key, "raw": raw, "ok": True,
                            "action": action, "repairs": repairs})
        except ValueError as exc:
            actions.append({"key": key, "raw": raw, "ok": False, "error": str(exc)})

    plans = []
    for key, case in PLAN_CASES.items():
        plan, fixes = normalize_plan(copy.deepcopy(case["plan"]), case["message"])
        plans.append({"key": key, "plan": case["plan"], "message": case["message"],
                      "normalized": plan, "fixes": fixes})

    inference = [
        {"message": message,
         "file_target": infer_file_target(message),
         "manifest": infer_project_manifest(message)}
        for message in INFERENCE_MESSAGES
    ]

    transcripts = []
    for key, case in TRANSCRIPT_CASES.items():
        transcripts.append({
            "key": key,
            "message": case["message"],
            "transcript": case["transcript"],
            "files_written": files_written(case["transcript"], FILE_CREATE_ACTIONS),
            "artifact_checklist": artifact_checklist(case["transcript"], FILE_CREATE_ACTIONS),
            "requirement_coverage": requirement_coverage(
                case["message"], case["transcript"], FILE_CREATE_ACTIONS
            ),
            "compact_window_2": compact_transcript(case["transcript"], window=2, result_chars=40),
        })

    truncated = [
        {"key": case["key"], "limit": case["limit"], "value": case["value"],
         "truncated": _truncate_strings(case["value"], case["limit"])}
        for case in TRUNCATE_CASES
    ]

    learnings = [
        {"input": case, "kept": filter_learnings(case)} for case in LEARNING_CASES
    ]

    documents = [
        {**case, "target": document_output_target(case["tool"], case["filename"])}
        for case in DOCUMENT_TARGET_CASES
    ]

    # ``env`` is passed explicitly: the Rust twin reads the ambient process
    # environment, and a golden that inherited this machine's would be a
    # machine-specific value in a committed fixture.
    profiles = []
    for override in PROFILE_OVERRIDES:
        env = {"LATTICEAI_AGENT_PROFILE": override} if override else {}
        for model_id in PROFILE_MODEL_IDS:
            profiles.append({
                "override": override,
                "model_id": model_id,
                "size_b": model_size_b(model_id),
                "profile": profile_for_model(model_id, env=env).__dict__,
            })
    profiles.append({
        "override": "",
        "model_id": None,
        "size_b": model_size_b(""),
        "profile": profile_for_model(None, env={}).__dict__,
    })

    return normalize({
        "schema": SCHEMA,
        "extract_action_details": actions,
        "normalize_plan": plans,
        "inference": inference,
        "transcript_helpers": transcripts,
        "truncate_strings": truncated,
        "filter_learnings": learnings,
        "document_targets": documents,
        "agent_profiles": profiles,
        "budgets": {
            "phase": PhaseBudgets().__dict__,
            "transcript": TranscriptBudget().__dict__,
        },
    }, NO_ROOT)


# ── the scripted ports ────────────────────────────────────────────────────────
class ScriptedLLM:
    """`deps.generate_as`, answering a queue of recorded completions."""

    def __init__(self, outputs: List[str]) -> None:
        self.outputs = list(outputs)
        self.calls: List[Dict[str, Any]] = []

    async def generate_as(self, model_id: Optional[str] = None, **kwargs: Any) -> str:
        text = self.outputs.pop(0) if self.outputs else ""
        self.calls.append({
            "model_id": model_id,
            "message": kwargs.get("message"),
            "temperature": kwargs.get("temperature"),
            "max_tokens": kwargs.get("max_tokens"),
        })
        return text

    async def generate(self, **kwargs: Any) -> str:
        return await self.generate_as(None, **kwargs)


class ScriptedGovernor:
    """`deps.change_governor`, answering one fixed verdict."""

    governed_tools = frozenset({"write_file", "edit_file"})

    def __init__(self, verdict: Optional[Dict[str, Any]]) -> None:
        self.verdict = verdict

    def review(self, name: str, args: Dict[str, Any], **kwargs: Any) -> Optional[Dict[str, Any]]:
        return copy.deepcopy(self.verdict)


def build_deps(
    root: Path,
    llm: ScriptedLLM,
    *,
    governor: Optional[ScriptedGovernor],
    tool_calls: List[Dict[str, Any]],
    audit: List[Dict[str, Any]],
) -> AgentDeps:
    """The real ports, wired to the throwaway workspace."""
    registry = tools.DEFAULT_TOOL_REGISTRY
    service = tool_dispatch.DEFAULT_TOOL_DISPATCH_SERVICE

    def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        record: Dict[str, Any] = {"tool": name, "args": copy.deepcopy(args)}
        try:
            result = tools.execute_tool(name, args)
        except Exception as exc:  # noqa: BLE001 — recorded, then re-raised
            record["error"] = str(exc)
            tool_calls.append(record)
            raise
        record["result"] = result
        tool_calls.append(record)
        return result

    def record_audit(event: str, **details: Any) -> None:
        audit.append({"event": event, **details})

    return AgentDeps(
        generate_as=llm.generate_as,
        generate=llm.generate,
        execute_tool=execute_tool,
        policy_for=registry.policy_for,
        risk_level=registry.risk_level,
        check_role=lambda name, user: None,
        tool_governance=TOOL_GOVERNANCE,
        file_create_actions=FILE_CREATE_ACTIONS,
        recent_chat_context=lambda **kwargs: "",
        clear_history=lambda keep_last: {"ok": True, "kept": keep_last},
        knowledge_save=lambda *a, **k: None,
        audit=record_audit,
        planner_prompt="",
        executor_prompt="",
        critic_prompt="",
        memory_updater_prompt="",
        agent_root=root,
        rollback_file=service.rollback_file,
        snapshot_file=service.snapshot_file,
        restore_snapshot=service.restore_snapshot,
        hooks=None,
        change_governor=governor,
        phase_budgets=PhaseBudgets(),
        transcript_budget=TranscriptBudget(),
    )


# ── verification mapping grid ─────────────────────────────────────────────────
#: `(verdict, next_state)` pairs the mapping table distinguishes.
VERDICT_PAIRS = [
    ("PASS", "DONE"), ("PASS", "COMPLETE"), ("PASS", "EXECUTING"), ("PASS", ""),
    ("FAIL", "DONE"), ("FAIL", "EXECUTING"), ("FAIL", "RETRY"), ("FAIL", "ROLLBACK"),
    ("FAIL", "FAILED"), ("FAIL", "SOMETHING_ELSE"), ("", "DONE"),
]

EVIDENCE_STEP = {"state": "EXECUTING", "action": "write_file", "args": {"path": "index.html"},
                 "result": {"path": "index.html", "bytes": 4}}
NO_EVIDENCE_STEP = {"state": "EXECUTING", "action": "final", "thoughts": "t"}


async def verification_rows(root: Path) -> List[Dict[str, Any]]:
    """The verdict mapping, from the real `verify()`."""
    rows: List[Dict[str, Any]] = []
    for verdict, next_state in VERDICT_PAIRS:
        for evidence in (True, False):
            for message in ("make a note", "todo 앱 html css js 만들어줘"):
                for retry_count in (0, 3):
                    body = json.dumps(
                        {"action": "verdict", "verdict": verdict, "next_state": next_state,
                         "reason": "because", "corrections": ["be specific"], "confidence": 0.5},
                        ensure_ascii=False,
                    )
                    llm = ScriptedLLM([body])
                    ctx = AgentRunContext()
                    ctx.trace = LoopTrace()
                    ctx.retry_count = retry_count
                    ctx.transcript = [copy.deepcopy(
                        EVIDENCE_STEP if evidence else NO_EVIDENCE_STEP
                    )]
                    runtime = SingleAgentRuntime(build_deps(
                        root, llm, governor=None, tool_calls=[], audit=[]
                    ))
                    request = AgentRequest(message=message)
                    await runtime.verify(ctx, request, "Korean", "owner@example.com", max_retry=3)
                    rows.append({
                        "verdict": verdict, "next_state": next_state, "evidence": evidence,
                        "message": message, "retry_count": retry_count,
                        "final_state": ctx.state.value,
                        "final_message": ctx.final_message,
                        "retry_count_after": ctx.retry_count,
                        "transcript": normalize(ctx.transcript, root),
                    })
    # The unparseable critic: one strict retry, then fail closed.
    for outputs, key in (
        (["prose", "still prose"], "never_parses"),
        (["prose", '{"action": "v", "verdict": "PASS", "next_state": "DONE", "reason": "r"}'],
         "strict_retry_recovers"),
    ):
        llm = ScriptedLLM(list(outputs))
        ctx = AgentRunContext()
        ctx.trace = LoopTrace()
        ctx.transcript = [copy.deepcopy(EVIDENCE_STEP)]
        runtime = SingleAgentRuntime(build_deps(root, llm, governor=None, tool_calls=[], audit=[]))
        await runtime.verify(ctx, AgentRequest(message="make a note"), "Korean", "owner", max_retry=3)
        rows.append({
            "verdict": key, "next_state": "", "evidence": True, "message": "make a note",
            "retry_count": 0, "final_state": ctx.state.value,
            "final_message": ctx.final_message, "retry_count_after": ctx.retry_count,
            "transcript": normalize(ctx.transcript, root),
            "llm_calls": len(llm.calls),
            "temperatures": [call["temperature"] for call in llm.calls],
        })
    return rows


# ── run-store round trips ─────────────────────────────────────────────────────
def run_store_rows() -> List[Dict[str, Any]]:
    """`serialize_run_context` / `restore_run_context`, field for field."""
    rows: List[Dict[str, Any]] = []

    full = AgentRunContext()
    full.state = AgentState.WAITING_APPROVAL
    full.plan = {"goal": "g", "steps": [{"action": "write_file"}]}
    full.transcript = [{"state": "PLANNING", "goal": "g"}]
    full.retry_count = 2
    full.state_history = ["PLANNING", "WAITING_APPROVAL"]
    full.corrections = ["reply with JSON"]
    full.final_message = "paused"
    full.rollback_log = [{"path": "a.md", "existed": False}]
    full.executing_model = "m-exec"
    full.reviewing_model = "m-review"
    full.approved_by_human = True
    full.permission_mode = "trusted"
    full.trace = LoopTrace(clock=lambda: "PINNED")
    full.trace.llm_call("plan", model="m-exec")
    full.trace.repair("plan", repairs=["fence"])
    rows.append({"key": "full", "serialized": serialize_run_context(full)})

    empty = AgentRunContext()
    rows.append({"key": "empty", "serialized": serialize_run_context(empty)})

    for key, payload in (
        ("unknown_state", {"state": "SOMETHING_NEW"}),
        ("no_state", {}),
        ("null_state", {"state": None}),
        ("blank_mode", {"state": "EXECUTING", "permission_mode": ""}),
        ("kept_mode", {"state": "EXECUTING", "permission_mode": "bypass"}),
        ("truthy_approval", {"approved_by_human": 1}),
    ):
        rows.append({"key": f"restore_{key}", "payload": payload,
                     "restored": serialize_run_context(restore_run_context(payload))})

    for row in rows:
        if "serialized" in row:
            row["round_trip"] = serialize_run_context(restore_run_context(row["serialized"]))
    return normalize(rows, NO_ROOT)


# ── end-to-end trajectories ───────────────────────────────────────────────────
def plan_json(goal: str, steps: List[Dict[str, Any]]) -> str:
    return json.dumps(
        {"action": "plan", "goal": goal, "steps": steps, "estimated_steps": max(1, len(steps)),
         "requires_approval": False, "rollback_strategy": "none"},
        ensure_ascii=False,
    )


def action_json(**payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def verdict_json(verdict: str, next_state: str, reason: str = "checked") -> str:
    return json.dumps(
        {"action": "verdict", "verdict": verdict, "next_state": next_state,
         "reason": reason, "corrections": []},
        ensure_ascii=False,
    )


WRITE_STEP = [{"action": "write_file", "args": {"path": "note.md"}, "description": "the note"}]

#: Seven trajectories: between them they reach every terminal state by every
#: route the loop has, under all three permission modes.
SCENARIOS: Dict[str, Dict[str, Any]] = {
    "clean_done_trusted": {
        "mode": "trusted", "message": "make a note", "seed": {},
        "governor_verdict": None,
        "script": [
            plan_json("make a note", WRITE_STEP),
            action_json(thoughts="writing", action="write_file",
                        args={"path": "note.md", "content": "# Note\n\nhello\n"}),
            action_json(action="final", message="파일을 만들었습니다."),
            verdict_json("PASS", "DONE", "the file exists"),
        ],
    },
    "strict_proposal_pause": {
        "mode": "strict", "message": "update the note", "seed": {"note.md": "original\n"},
        "governor_verdict": {"decision": "proposed", "proposal": {"id": "prop-1"},
                             "classification": {"change_class": "mutation"}},
        "script": [
            plan_json("update the note", WRITE_STEP),
            action_json(thoughts="rewriting", action="write_file",
                        args={"path": "note.md", "content": "rewritten\n"}),
            action_json(action="final", message="제안으로 저장했습니다."),
            verdict_json("PASS", "DONE", "staged for review"),
        ],
    },
    "parse_budget_exhaustion": {
        "mode": "trusted", "message": "make a note", "seed": {},
        "governor_verdict": None,
        "script": [
            plan_json("make a note", WRITE_STEP),
            "I will now write the file for you.",
            "Writing it now, one moment.",
            "All done, I think.",
            verdict_json("PASS", "DONE", "looks fine"),
        ],
    },
    "repeated_create_guard": {
        "mode": "trusted", "message": "make a note", "seed": {},
        "governor_verdict": None,
        "script": [
            plan_json("make a note", WRITE_STEP),
            action_json(action="write_file", args={"path": "note.md", "content": "body\n"}),
            action_json(action="write_file", args={"path": "note.md", "content": "body\n"}),
            verdict_json("PASS", "DONE", "written once"),
        ],
    },
    "verify_retry_then_failed": {
        "mode": "trusted", "message": "make a note", "seed": {},
        "governor_verdict": None,
        "script": [
            plan_json("make a note", WRITE_STEP),
            action_json(action="write_file", args={"path": "note.md", "content": "v1\n"}),
            action_json(action="final", message="done"),
            verdict_json("FAIL", "EXECUTING", "not good enough"),
            action_json(action="final", message="done"),
            verdict_json("FAIL", "EXECUTING", "still not"),
            action_json(action="final", message="done"),
            verdict_json("FAIL", "EXECUTING", "no"),
            action_json(action="final", message="done"),
            verdict_json("FAIL", "EXECUTING", "give up"),
        ],
    },
    "verify_pass_no_evidence": {
        "mode": "trusted", "message": "tell me about the notes", "seed": {},
        "governor_verdict": None,
        "script": [
            plan_json("answer the question", []),
            action_json(action="final", message="여기 답변입니다."),
            verdict_json("PASS", "DONE", "answered"),
        ],
    },
    "rollback_path": {
        "mode": "trusted", "message": "update the note", "seed": {"note.md": "original\n"},
        "governor_verdict": None,
        "script": [
            plan_json("update the note", WRITE_STEP),
            action_json(action="write_file", args={"path": "note.md", "content": "broken\n"}),
            action_json(action="final", message="done"),
            verdict_json("FAIL", "ROLLBACK", "the change is wrong"),
        ],
    },
    "blocked_fail_closed_strict": {
        "mode": "strict", "message": "delete the note", "seed": {"note.md": "original\n"},
        "governor_verdict": None,
        "script": [
            # The plan itself is auto-approvable (a read); the *executor* then
            # reaches for a destructive tool, which is where the gate fires.
            plan_json("delete the note", [{"action": "read_file", "args": {"path": "note.md"},
                                           "description": "look at it first"}]),
            action_json(action="delete_file", args={"path": "note.md"}),
            action_json(action="final", message="삭제하지 못했습니다."),
            verdict_json("PASS", "DONE", "nothing was deleted"),
        ],
    },
    "blocked_breaker_bypass": {
        "mode": "bypass", "message": "fix the hosts file", "seed": {},
        "governor_verdict": None,
        "script": [
            plan_json("fix the hosts file", [{"action": "read_file", "args": {"path": "note.md"},
                                              "description": "look first"}]),
            # The registry rewrites a write aimed at a blocked system prefix
            # into a destructive policy, and the breaker refuses it — in
            # `bypass`, which is the whole point of a mode-invariant gate.
            action_json(action="write_file", args={"path": "/etc/hosts", "content": "x\n"}),
            action_json(action="final", message="시스템 파일은 건드리지 않았습니다."),
            verdict_json("FAIL", "FAILED", "nothing was changed"),
        ],
    },
    "approval_pause_strict": {
        "mode": "strict", "message": "run the tests", "seed": {},
        "governor_verdict": None,
        "pause_expected": True,
        "script": [
            plan_json("run the tests", [{"action": "run_command",
                                         "args": {"command": "ls"}, "description": "list"}]),
        ],
    },
}


async def trajectory(key: str, scenario: Dict[str, Any], base: Path) -> Dict[str, Any]:
    """Drive the real runtime through one scenario and record what happened."""
    root = base / key / "agent_workspace"
    with use_workspace(root) as resolved:
        for name, body in scenario["seed"].items():
            target = resolved / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
        # A scripted write whose content the artifact pipeline would rewrite
        # would make this trajectory untestable against the native loop, where
        # sanitation is the worker's job. Prove it does not, at build time.
        for output in scenario["script"]:
            content = _scripted_write_content(output)
            if content is not None:
                _, meta = sanitize_write_content("note.md", content, user_request=scenario["message"])
                if meta.get("sanitized"):
                    raise SystemExit(
                        f"scenario {key}: scripted content is rewritten by "
                        "sanitize_write_content; pick content the pipeline leaves alone"
                    )

        tool_calls: List[Dict[str, Any]] = []
        audit: List[Dict[str, Any]] = []
        llm = ScriptedLLM(scenario["script"])
        governor = (
            ScriptedGovernor(scenario["governor_verdict"])
            if scenario.get("governor_verdict") is not None or scenario["mode"] == "strict"
            else ScriptedGovernor(None)
        )
        deps = build_deps(resolved, llm, governor=governor, tool_calls=tool_calls, audit=audit)
        runtime = SingleAgentRuntime(deps)

        request = AgentRequest(message=scenario["message"], user_email="owner@example.com")
        ctx = AgentRunContext()
        ctx.trace = LoopTrace()
        ctx.permission_mode = scenario["mode"]
        ctx.state = AgentState.PLANNING
        ctx.state_history.append(ctx.state.value)
        await runtime.plan(ctx, request, "Korean", "owner@example.com", model_id=None)
        requirements = runtime.approval_requirements(ctx)
        paused = bool(requirements["requires_approval"])
        if paused:
            ctx.state_history.append(AgentState.WAITING_APPROVAL.value)
        else:
            runtime.approve(ctx, "owner@example.com", approved_by_human=False)
            await runtime.run_to_completion(
                ctx, request, "Korean", "owner@example.com",
                max(1, min(request.max_steps, 50)), 3,
            )
        if paused != bool(scenario.get("pause_expected")):
            raise SystemExit(f"scenario {key}: pause={paused}, expected the opposite")

        return {
            "key": key,
            "mode": scenario["mode"],
            "message": scenario["message"],
            "seed": scenario["seed"],
            "scripted_llm": scenario["script"],
            "governor_verdict": scenario["governor_verdict"],
            "req": {"message": scenario["message"], "user_email": "owner@example.com",
                    "max_steps": request.max_steps, "temperature": request.temperature},
            "paused": paused,
            "approval_requirements": normalize(requirements, resolved),
            "final_state": ctx.state.value,
            "final_message": normalize(ctx.final_message, resolved),
            "state_history": ctx.state_history,
            "transcript": normalize(ctx.transcript, resolved),
            "rollback_log": normalize(ctx.rollback_log, resolved),
            "loop": ctx.trace.summary(),
            "tool_calls": normalize(tool_calls, resolved),
            "audit": normalize(audit, resolved),
            "llm_calls": len(llm.calls),
            "unused_script": len(llm.outputs),
        }


def _scripted_write_content(output: str) -> Optional[str]:
    """The `content` of a scripted `write_file` action, when it is one."""
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("action") != "write_file":
        return None
    content = (payload.get("args") or {}).get("content")
    return content if isinstance(content, str) else None


def policy_payload() -> Dict[str, Any]:
    """The real registry, as the data the native loop takes as input."""
    return {
        "tools": {name: dict(policy) for name, policy in sorted(TOOL_GOVERNANCE.items())},
        "default": dict(TOOL_GOVERNANCE_DEFAULT),
        "blocked_write_prefixes": list(LOCAL_WRITE_BLOCKED_PREFIXES),
    }


def manifest_payload() -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "scenarios": sorted(SCENARIOS),
        "raw_actions": sorted(RAW_ACTIONS),
        "plan_cases": sorted(PLAN_CASES),
        "normalization": [
            "the absolute workspace root becomes <AGENT_ROOT>",
            "keys named `at` are dropped (trace timestamps)",
            "keys named `stderr` are dropped (git text is version-specific)",
            f"a string starting with {DECODER_DETAIL_PREFIX!r} keeps only that prefix",
        ],
        "constants": {
            "file_create_actions": sorted(FILE_CREATE_ACTIONS),
            "scoped_knowledge_tools": sorted(SCOPED_KNOWLEDGE_TOOLS),
            "governed_tools": sorted(ScriptedGovernor.governed_tools),
            "phase_budgets": PhaseBudgets().__dict__,
            "transcript_budget": TranscriptBudget().__dict__,
            "max_state_history": 200,
            "max_retry": 3,
            "compact_max_params_b": COMPACT_MAX_PARAMS_B,
        },
    }


async def build_async(base: Path) -> Dict[str, Any]:
    """Everything the goldens hold, as `{filename: payload}`."""
    verify_root = base / "verify" / "agent_workspace"
    with use_workspace(verify_root) as resolved:
        verification = await verification_rows(resolved)
    trajectories = [await trajectory(key, SCENARIOS[key], base) for key in sorted(SCENARIOS)]
    return {
        "manifest.json": manifest_payload(),
        "policies.json": policy_payload(),
        "helpers.json": helper_rows(),
        "verification.json": {"schema": SCHEMA, "cases": verification},
        "run_store.json": {"schema": SCHEMA, "cases": run_store_rows()},
        "trajectories.json": {"schema": SCHEMA, "cases": trajectories},
    }


def build(base: Optional[Path] = None) -> Dict[str, Any]:
    """Synchronous entry point, for the generator and the contract test alike."""
    if base is None:
        base = Path(tempfile.mkdtemp(prefix="agent-loop-fixtures-"))
    return asyncio.run(build_async(Path(base)))


def write(payloads: Dict[str, Any]) -> List[str]:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (GOLDEN_DIR / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return sorted(payloads)


def main() -> int:
    written = write(build())
    for name in written:
        path = GOLDEN_DIR / name
        print(f"wrote {path.relative_to(REPO_ROOT)} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
