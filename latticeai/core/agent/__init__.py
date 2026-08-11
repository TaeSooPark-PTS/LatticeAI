"""Single-agent runtime — the Discover→Plan→Implement→Verify state machine.

This package is the deep single-agent loop: a small interface (``AgentDeps``
ports + ``SingleAgentRuntime.run_to_completion``) over the whole role-phased
state machine (planner → executor → critic → rollback → memory). It carries no
FastAPI, no globals, and no I/O of its own — every collaborator is injected
through ``AgentDeps``.

Two adapters justify the seam:

* production wires ``AgentDeps`` from ``latticeai.server_app``'s ``LLMRouter``, governance
  map, audit log, and prompts;
* tests pass fake ports (an LLM that returns canned JSON, a recording tool
  executor) and drive a full PLAN→EXECUTE→VERIFY→DONE cycle without a server.

HTTP concerns — request parsing, chat-history persistence, response shaping,
scheduling the background memory update — stay in the app layer. This package
only owns the state machine.

v10.0.1 split the pure helpers and the state vocabulary out to sibling modules
(``agent_state``, ``agent_helpers``, ``agent_trace``, …). v11.3.0 finished the
job on what was left, turning the module into a package whose submodules are
one phase each:

* :mod:`.context` — ``AgentRunContext``, the state one run carries;
* :mod:`.deps` — ``AgentDeps``, the ports;
* :mod:`.planning` / :mod:`.execution` / :mod:`.verification` / :mod:`.recovery`
  — the four phase mixins;
* :mod:`.runtime` — ``SingleAgentRuntime``, which composes them and drives the
  loop;
* :mod:`._contract` — the typing-only seam the mixins share.

Every name this module exported still resolves from ``latticeai.core.agent``.

Stubbing note: a name rebound *here* changes only this module's binding — the
submodule that calls it holds its own, so a test standing in for a collaborator
patches the submodule that reads it.
"""

from __future__ import annotations

# The state vocabulary and the pure helpers live in sibling modules so the loop
# holds only the loop. They are re-exported (see ``__all__``) because callers —
# the HTTP layer, run_store, the eval harness, and the tests — have always
# imported them from here, and that contract does not change.
from latticeai.core.agent_helpers import PhaseBudgets as PhaseBudgets
from latticeai.core.agent_helpers import TranscriptBudget as TranscriptBudget
from latticeai.core.agent_helpers import _truncate_strings as _truncate_strings
from latticeai.core.agent_helpers import artifact_checklist as artifact_checklist
from latticeai.core.agent_helpers import compact_transcript as compact_transcript
from latticeai.core.agent_helpers import extract_action as extract_action
from latticeai.core.agent_helpers import (
    extract_action_details as extract_action_details,
)
from latticeai.core.agent_helpers import files_written as files_written
from latticeai.core.agent_helpers import filter_learnings as filter_learnings
from latticeai.core.agent_helpers import (
    format_artifact_checklist as format_artifact_checklist,
)
from latticeai.core.agent_helpers import (
    format_requirement_coverage as format_requirement_coverage,
)
from latticeai.core.agent_helpers import normalize_plan as normalize_plan
from latticeai.core.agent_helpers import requirement_coverage as requirement_coverage
from latticeai.core.agent_state import AGENT_TERMINAL_STATES as AGENT_TERMINAL_STATES
from latticeai.core.agent_state import AgentState as AgentState

from .context import AgentRunContext as AgentRunContext
from .deps import AgentDeps as AgentDeps
from .runtime import SingleAgentRuntime as SingleAgentRuntime

__all__ = [
    # this module
    "AgentDeps",
    "AgentRunContext",
    "SingleAgentRuntime",
    # re-exported from agent_state
    "AGENT_TERMINAL_STATES",
    "AgentState",
    # re-exported from agent_helpers
    "PhaseBudgets",
    "TranscriptBudget",
    "artifact_checklist",
    "compact_transcript",
    "extract_action",
    "extract_action_details",
    "files_written",
    "filter_learnings",
    "format_artifact_checklist",
    "format_requirement_coverage",
    "normalize_plan",
    "requirement_coverage",
]
