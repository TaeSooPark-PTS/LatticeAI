"""Weak-model prompt enrichment (Track 4).

The failure this addresses is not a wrong file — it is *no file*: a small local
model that explains what it would write and never calls ``write_file``. The
enrichment is additive on purpose, so the guarantees are:

* the shipped role prompts are untouched;
* the compact profile gets the numbered short form, everything else the prose;
* an empty Self-Model adds nothing at all.
"""

from __future__ import annotations

from types import SimpleNamespace

from latticeai.core.agent_profiles import COMPACT, STANDARD
from latticeai.core.agent_prompts import (
    AGENT_SYSTEM_PROMPT,
    COMPACT_FILE_TASK_HINTS,
    EXECUTOR_PROMPT,
    FILE_TASK_HINTS,
    executor_prompt_for,
    file_task_hints,
)


def test_the_shipped_role_prompts_are_unchanged():
    assert AGENT_SYSTEM_PROMPT is EXECUTOR_PROMPT
    assert "FILE TASKS" not in EXECUTOR_PROMPT


def test_a_weak_model_gets_the_short_form():
    assert file_task_hints(COMPACT) == COMPACT_FILE_TASK_HINTS.strip()
    assert file_task_hints("compact") == COMPACT_FILE_TASK_HINTS.strip()
    assert file_task_hints(SimpleNamespace(name="COMPACT")) == COMPACT_FILE_TASK_HINTS.strip()
    assert file_task_hints(STANDARD) == FILE_TASK_HINTS.strip()
    assert file_task_hints(None) == FILE_TASK_HINTS.strip()


def test_the_executor_prompt_gains_hints_and_keeps_its_base():
    prompt = executor_prompt_for(profile=COMPACT)

    assert prompt.startswith(EXECUTOR_PROMPT.rstrip())
    assert "write_file" in prompt
    assert "1. write_file" in prompt
    assert "ABOUT THE USER" not in prompt  # nothing known → nothing injected


def test_a_known_user_is_described_once_and_only_when_known():
    with_profile = executor_prompt_for(
        "BASE", profile=STANDARD, self_model_summary="- 선호: 로컬 모델"
    )
    without = executor_prompt_for("BASE", profile=STANDARD, self_model_summary="   ")

    assert with_profile.startswith("BASE\n\n")
    assert "ABOUT THE USER" in with_profile
    assert "로컬 모델" in with_profile
    assert "ABOUT THE USER" not in without
    assert executor_prompt_for("BASE") == without


def test_the_agent_loop_actually_sends_the_hints():
    # The wiring, not just the builder: the executor turn's prompt carries them.
    from latticeai.core.agent import AgentRunContext, SingleAgentRuntime
    from tests.unit.test_agent_loop_l4_l5_l7 import _deps  # existing loop fixture

    runtime = SingleAgentRuntime(_deps())
    ctx = AgentRunContext()
    ctx.plan = {"goal": "write a file", "steps": []}
    request = SimpleNamespace(
        message="report.md 를 만들어줘",
        conversation_id=None,
        planning_model=None,
        executing_model=None,
        reviewing_model=None,
    )

    standard = runtime._executor_context(ctx, request, "ko", "tester", None)
    compact = runtime._executor_context(ctx, request, "ko", "tester", None, COMPACT)

    assert "FILE TASKS" in standard
    assert "FILE TASKS (short form)" in compact
    assert "PLAN:" in standard  # the rest of the prompt is unchanged
