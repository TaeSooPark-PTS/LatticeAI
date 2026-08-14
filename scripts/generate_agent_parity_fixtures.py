#!/usr/bin/env python3
"""Build the committed Python↔Rust **safety kernel** parity fixtures (v11.5.0).

``rust/lattice-agent`` owns the diagram's Tools/Sandbox/Permission labels: it
decides, natively, whether a tool call may run and whether a command string is
safe to execute. A decision port is only worth having if something keeps proving
it still decides the same thing, so this script is the Python half of that
proof. It runs the **real** kernel functions

* ``latticeai.core.permission_mode`` — ``normalize_mode``, ``is_circuit_breaker``,
  ``effective_auto_approve``, ``should_stage_proposal``, ``plan_requires_approval``,
  ``mode_contract``;
* ``latticeai.core.agent_permission`` — ``block_reason_for_tool``,
  ``non_auto_plan_steps``;
* ``latticeai.core.tool_governor`` — ``classify_tool_call``;
* ``latticeai.tools.commands.run_command`` — the command sandbox validator, and
  its real execution for a small read-only set;

over a decision grid built from the **real** per-tool policy table
(``latticeai.core.tool_registry.TOOL_GOVERNANCE``, read through
``ToolRegistry.policy_for`` so the blocked-prefix override is real too), and
writes every verdict to ``rust/fixtures/agent/golden/``.

Two consumers read what it writes:

* ``tests/unit/test_agent_kernel_parity_contract.py`` re-runs the Python kernel
  over the same grid and asserts the committed goldens still hold — so loosening
  a gate in Python fails loudly instead of silently invalidating the contract
  the Rust side is pinned to;
* ``rust/lattice-agent/tests/parity.rs`` runs the Rust kernel against the same
  goldens, exactly.

Determinism is the design constraint: no clock, no network, no machine-specific
path in any golden. The command fixtures run inside a throwaway workspace whose
layout is *described* in the manifest (``tree``) so the Rust suite can build the
identical tree, and the absolute root is written back out as ``<AGENT_ROOT>``.
The ``which(1)`` lookup is deliberately outside the goldens: whether ``rg`` is
installed is a property of the machine, not of the validator.

Usage::

    .venv/bin/python scripts/generate_agent_parity_fixtures.py
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import latticeai.tools as tools  # noqa: E402
from latticeai.core.agent_permission import (  # noqa: E402
    block_reason_for_tool,
    non_auto_plan_steps,
)
from latticeai.core.permission_mode import (  # noqa: E402
    COMPUTER_CONTROL_TOOLS,
    COMPUTER_OBSERVATION_TOOLS,
    HARD_BLOCK_SANDBOXES,
    KNOWLEDGE_READ_TOOLS,
    WORKSPACE_WRITE_TOOLS,
    effective_auto_approve,
    is_circuit_breaker,
    mode_contract,
    normalize_mode,
    plan_requires_approval,
    should_stage_proposal,
)
from latticeai.core.tool_governor import (  # noqa: E402
    MUTATING_TOOL_INVENTORY,
    PROPOSAL_CAPABLE_TOOLS,
    classify_tool_call,
)
from latticeai.core.tool_registry import TOOL_GOVERNANCE  # noqa: E402
from latticeai.tools import commands as command_tools  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "rust" / "fixtures" / "agent"
GOLDEN_DIR = FIXTURE_DIR / "golden"

SCHEMA = "agent-kernel-parity/v1"
MODES: List[str] = ["strict", "trusted", "bypass"]

#: LANG/LC_ALL leak into the child environment from the parent, so they are
#: pinned while the fixtures are built and recorded in the manifest.
PINNED_ENV: Dict[str, str] = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


# ── the decision grid ─────────────────────────────────────────────────────────
#: Every name any kernel table knows about: the dispatchable registry, the
#: governance table, the mutating inventory, and the four permission-mode tool
#: sets (which name knowledge-graph tools the registry does not dispatch).
def tool_universe() -> List[str]:
    return sorted(
        set(tools.registered_tools())
        | set(TOOL_GOVERNANCE)
        | set(MUTATING_TOOL_INVENTORY)
        | set(KNOWLEDGE_READ_TOOLS)
        | set(WORKSPACE_WRITE_TOOLS)
        | set(COMPUTER_OBSERVATION_TOOLS)
        | set(COMPUTER_CONTROL_TOOLS)
    )


#: Argument shapes chosen for the branches they reach, not for realism:
#: the two path keys ``is_circuit_breaker`` reads, both command keys, the
#: blocked-prefix override that rewrites a write policy into a destructive one,
#: the ``rstrip("/")`` and backslash-normalisation branches of the root guard,
#: and a target that exists (mutation) versus one that does not (additive).
ARG_VARIANTS: Dict[str, Dict[str, Any]] = {
    "none": {},
    "benign_path": {"path": "notes/todo.md"},
    "existing_path": {"path": "notes/existing.md"},
    "filename_doc": {"filename": "report.docx"},
    "blocked_prefix": {"path": "/etc/hosts"},
    "root_path": {"path": "/"},
    "home_tilde_slash": {"path": "~/"},
    "windows_home": {"path": "\\home"},
    "users_root": {"path": "/Users"},
    "traversal": {"path": "../../etc/passwd"},
    "rm_rf_root": {"command": "rm -rf /"},
    "rm_rf_home_upper": {"cmd": "RM -RF $HOME"},
    "long_path": {"path": "deep/" + ("a" * 180) + ".md"},
}

#: Paths ``classify_tool_call``'s injected ``path_exists`` answers True for.
EXISTING_PATHS = frozenset({"notes/existing.md", "report.docx", "/etc/hosts"})

#: ``effective_auto_approve``'s other axis. ``None`` is a member of the trusted
#: branch's accepted set, so it is a case and not an absence.
CHANGE_CLASSES: List[Optional[str]] = [
    None, "read", "additive", "mutation", "destructive", "exec",
]

#: Mode inputs, including every alias plus the shapes that reach the
#: ``str(value or "")`` fallback.
NORMALIZE_INPUTS: List[Any] = [
    "strict", "default", "manual", "trusted", "acceptedits", "accept_edits",
    "workspace", "bypass", "bypasspermissions", "bypass_permissions", "yolo",
    "dangerously-skip-permissions", "acceptEdits", "  TRUSTED  ", "BYPASS",
    "Dangerously-Skip-Permissions", "", "   ", "junk", "strictly", "read-only",
    None, False, True, 0, 1, [], {},
]

#: Plans exercising the strict governor-tool skip, the missing-``action`` skip,
#: an unknown tool falling back to the default policy, and the ``plan_flag``.
PLAN_CASES: List[Dict[str, Any]] = [
    {"key": "empty", "steps": [], "governed": [], "plan_flag": False},
    {"key": "reads_only", "governed": [], "plan_flag": False,
     "steps": [{"action": "read_file"}, {"action": "list_dir"}]},
    {"key": "reads_plan_flag", "governed": [], "plan_flag": True,
     "steps": [{"action": "read_file"}]},
    {"key": "workspace_writes", "governed": [], "plan_flag": False,
     "steps": [{"action": "write_file"}, {"action": "edit_file"}, {"action": "todo_write"}]},
    {"key": "governed_writes", "governed": ["write_file", "edit_file"], "plan_flag": False,
     "steps": [{"action": "write_file"}, {"action": "edit_file"}, {"action": "run_command"}]},
    {"key": "exec_and_desktop", "governed": [], "plan_flag": False,
     "steps": [{"action": "run_command"}, {"action": "computer_click"},
               {"action": "computer_screenshot"}]},
    {"key": "unknown_tool", "governed": [], "plan_flag": False,
     "steps": [{"action": "not_a_tool"}, {"action": "knowledge_search"}]},
    {"key": "missing_action", "governed": [], "plan_flag": False,
     "steps": [{"description": "no action key"}, {"action": ""}, {"action": "local_read"}]},
]


# ── the command-sandbox workspace ────────────────────────────────────────────
#: The throwaway ``AGENT_ROOT`` the command fixtures run inside, described so the
#: Rust suite builds the byte-identical tree. ``outside`` entries are written
#: beside the root — the only way a symlink escape can be a real escape.
TREE: List[Dict[str, Any]] = [
    {"kind": "outside", "path": "outside_secret.txt", "content": "top secret\n"},
    {"kind": "dir", "path": "notes"},
    {"kind": "file", "path": "notes/a.txt", "content": "alpha\nbeta\ngamma\n"},
    {"kind": "dir", "path": "a b"},
    {"kind": "file", "path": "a b/c.txt", "content": "spaced\n"},
    {"kind": "file", "path": "quoted name.txt", "content": "quoted\n"},
    {"kind": "dir", "path": "노트"},
    {"kind": "file", "path": "노트/메모.txt", "content": "한글\n"},
    {"kind": "dir", "path": "sub"},
    {"kind": "file", "path": "sub/inner.txt", "content": "inner\n"},
    {"kind": "file", "path": "a\\b", "content": "backslash\n"},
    {"kind": "symlink", "path": "inside_link", "target": "notes/a.txt"},
    {"kind": "symlink", "path": "escape_link", "target": "../outside_secret.txt"},
    #: 3,000 lines of ``%07d\n`` = 24,000 characters, so ``cat`` of it is exactly
    #: twice ``MAX_COMMAND_OUTPUT`` and the tail-slice is observable.
    {"kind": "lines", "path": "big.txt", "count": 3000},
]

#: ``(key, command, cwd)``. Validation only — nothing here is executed.
COMMAND_CASES: List[Tuple[str, str, Optional[str]]] = [
    ("empty", "", None),
    ("whitespace_only", "   ", None),
    ("plain_ls", "ls", None),
    ("ls_flags", "ls -la", None),
    ("pwd", "pwd", None),
    ("absolute_executable", "/bin/ls", None),
    ("relative_executable", "./ls", None),
    ("trailing_slash_executable", "ls/", None),
    ("empty_executable", "'' notes", None),
    ("blocked_rm", "rm -rf /", None),
    ("blocked_sudo", "sudo ls", None),
    ("not_allowlisted", "echo hi", None),
    ("git_status", "git status", None),
    ("git_log", "git log --oneline", None),
    ("pipe", "cat notes/a.txt | wc -l", None),
    ("and_and", "cat notes/a.txt && ls", None),
    ("semicolon", "cat notes/a.txt; ls", None),
    ("redirect_out", "cat notes/a.txt > out.txt", None),
    ("redirect_in", "wc -l < notes/a.txt", None),
    ("dollar_paren", "cat $(ls)", None),
    ("backtick", "cat `ls`", None),
    ("or_or", "ls || ls", None),
    ("find_delete", "find . -delete", None),
    ("find_exec", "find . -name x -exec cat {} +", None),
    ("find_okdir", "find . -okdir cat", None),
    ("find_ok_prefix_only", "find . -name -execute", None),
    ("find_plain", "find . -name inner.txt", None),
    ("rg_pre", "rg --pre cat foo", None),
    ("rg_pre_glob_eq", "rg --pre-glob=*.py foo", None),
    ("rg_pretty_is_fine", "rg --pretty foo", None),
    ("traversal", "cat ../outside_secret.txt", None),
    ("traversal_middle", "cat sub/../../outside_secret.txt", None),
    ("dotdot_alone", "cat ..", None),
    ("absolute_arg", "cat /etc/passwd", None),
    ("tilde_arg", "cat ~", None),
    ("tilde_path_arg", "cat ~/secret", None),
    ("dev_null_is_exempt", "cat /dev/null", None),
    ("bare_dash_flag", "cat -", None),
    ("empty_arg", "cat ''", None),
    ("dot_arg", "cat .", None),
    ("kv_plain_value", "ls --color=auto", None),
    ("kv_absolute_value", "ls --color=/etc", None),
    ("kv_traversal_value", "ls --color=../x", None),
    ("kv_inside_value", "ls --color=notes/a.txt", None),
    ("symlink_escape", "cat escape_link", None),
    ("symlink_inside", "cat inside_link", None),
    ("quoted_space", "cat 'quoted name.txt'", None),
    ("double_quoted_dir", 'cat "a b/c.txt"', None),
    ("escaped_space", "cat a\\ b/c.txt", None),
    ("backslash_value", "cat a\\\\b", None),
    ("korean_path", "cat 노트/메모.txt", None),
    ("unterminated_quote", "cat 'unterminated", None),
    ("dangling_escape", "cat trailing\\", None),
    ("missing_file_is_allowed", "cat missing.txt", None),
    ("cwd_subdir", "ls", "sub"),
    ("cwd_escape", "ls", "../"),
    ("cwd_absolute", "ls", "/etc"),
    ("cwd_missing", "ls", "nope"),
    ("cwd_is_a_file", "ls", "notes/a.txt"),
]

#: Commands that really run, in the throwaway workspace. Read-only, allow-listed,
#: and chosen so the bytes are identical on macOS and Linux — which rules out
#: ``wc`` (BSD pads its counts) and any listing whose order is collation
#: dependent. ``stderr`` is pinned only where it is empty on both.
EXECUTION_CASES: List[Tuple[str, str, Optional[str], bool]] = [
    ("pwd", "pwd", None, True),
    ("pwd_in_subdir", "pwd", "sub", True),
    ("cat_file", "cat notes/a.txt", None, True),
    ("cat_quoted", "cat 'quoted name.txt'", None, True),
    ("cat_double_quoted", 'cat "a b/c.txt"', None, True),
    ("cat_korean", "cat 노트/메모.txt", None, True),
    ("cat_backslash", "cat a\\\\b", None, True),
    ("cat_symlink_inside", "cat inside_link", None, True),
    ("head_two", "head -n 2 notes/a.txt", None, True),
    ("tail_one", "tail -n 1 notes/a.txt", None, True),
    ("ls_subdir", "ls notes", None, True),
    ("ls_cwd", "ls", "sub", True),
    ("find_one", "find . -name inner.txt", None, True),
    ("cat_truncates", "cat big.txt", None, True),
    ("missing_file_exit_code", "cat missing.txt", None, False),
]

#: ``_resolve_path`` — the file sandbox every file tool goes through.
PATH_CASES: List[Tuple[str, str]] = [
    ("empty", ""),
    ("dot", "."),
    ("benign", "notes/a.txt"),
    ("normalising", "notes/../notes/a.txt"),
    ("korean", "노트/메모.txt"),
    ("traversal", "../outside_secret.txt"),
    ("absolute_outside", "/etc/passwd"),
    ("absolute_inside", "<AGENT_ROOT>/notes/a.txt"),
    ("absolute_root", "<AGENT_ROOT>"),
    ("symlink_inside", "inside_link"),
    ("symlink_escape", "escape_link"),
    ("missing_but_inside", "notes/missing/deeper.txt"),
]

#: ``shlex.split`` edges. The validator splits before it decides anything, so a
#: divergence here is a divergence in every rule downstream of it.
SHLEX_CASES: List[str] = [
    "", "   ", "ls", "ls -la", "ls\t-la", "ls\n-la", "a b  c",
    "cat 'a b.txt'", 'cat "a b"', "cat a\\ b", 'cat "a\\nb"', "cat 'a\\nb'",
    "cat ''", 'cat ""', "''", '""', 'a"b"c', "x'y'z", "cat \"a\\\"b\"",
    "cat 'a\\'", "cat back\\\\", "cat \\'quoted\\'", "cat a\\\\b",
    "cat 노트/메모.txt", "cat '노트/메모.txt'", "  leading and trailing  ",
    "cat \"a\"b'c'", "cat '' ''", "cat $(ls)", "cat `ls`", "cat a=b --c=d",
    "cat 'unterminated", 'cat "unterminated', "cat trailing\\", 'cat "trailing\\',
]


# ── helpers ───────────────────────────────────────────────────────────────────
@contextmanager
def pinned_environment() -> Iterator[None]:
    """Pin the environment variables that leak into the sandboxed child."""
    previous = {key: os.environ.get(key) for key in PINNED_ENV}
    os.environ.update(PINNED_ENV)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def agent_root(root: Path) -> Iterator[Path]:
    """Point the real ``AGENT_ROOT`` at ``root`` for the duration.

    ``latticeai.tools`` holds the module global every path helper reads, and
    ``commands.py`` reaches it through ``tools.AGENT_ROOT``, so rebinding that
    one name redirects the whole sandbox.
    """
    original = tools.AGENT_ROOT
    tools.AGENT_ROOT = root.resolve()
    try:
        yield tools.AGENT_ROOT
    finally:
        tools.AGENT_ROOT = original


def build_tree(root: Path) -> None:
    """Materialise :data:`TREE`. Same spec the Rust suite builds from."""
    root.mkdir(parents=True, exist_ok=True)
    for node in TREE:
        kind = node["kind"]
        if kind == "outside":
            (root.parent / node["path"]).write_text(node["content"], encoding="utf-8")
        elif kind == "dir":
            (root / node["path"]).mkdir(parents=True, exist_ok=True)
        elif kind == "file":
            (root / node["path"]).write_text(node["content"], encoding="utf-8")
        elif kind == "lines":
            body = "".join(f"{index:07d}\n" for index in range(node["count"]))
            (root / node["path"]).write_text(body, encoding="utf-8")
        elif kind == "symlink":
            link = root / node["path"]
            if link.is_symlink():
                link.unlink()
            link.symlink_to(node["target"])
        else:  # pragma: no cover - the spec is closed
            raise ValueError(f"unknown tree node kind: {kind}")


class _Spawned(Exception):
    """Raised in place of ``subprocess.run`` so validation stops at the spawn."""

    def __init__(self, argv: List[str], cwd: Any, env: Dict[str, str]) -> None:
        super().__init__("spawn")
        self.argv = list(argv)
        self.cwd = cwd
        self.env = dict(env)


@contextmanager
def validation_only() -> Iterator[List[str]]:
    """Stop ``run_command`` at the spawn, and make ``which`` machine-independent.

    Whether ``rg`` is installed is a property of the machine; whether ``rg --pre``
    is refused is a property of the validator. Recording the second without the
    first is the whole point of this seam. Every ``which`` call is captured so
    the caller can assert the fixed PATH was the one searched.
    """
    searched: List[str] = []
    real_subprocess = command_tools.subprocess
    real_shutil = getattr(command_tools, "shutil", None)

    class _SubprocessShim:
        TimeoutExpired = subprocess.TimeoutExpired

        @staticmethod
        def run(argv: List[str], **kwargs: Any) -> None:
            raise _Spawned(argv, kwargs.get("cwd"), kwargs.get("env") or {})

    class _ShutilShim:
        @staticmethod
        def which(cmd: str, path: Optional[str] = None) -> str:
            searched.append(str(path))
            return f"<which>/{cmd}"

    command_tools.subprocess = _SubprocessShim  # type: ignore[assignment]
    command_tools.shutil = _ShutilShim  # type: ignore[assignment]
    try:
        yield searched
    finally:
        command_tools.subprocess = real_subprocess
        if real_shutil is None:
            delattr(command_tools, "shutil")
        else:
            command_tools.shutil = real_shutil


def _error(exc: BaseException) -> Dict[str, str]:
    kind = "tool" if isinstance(exc, tools.ToolError) else "shlex"
    return {"kind": kind, "message": str(exc)}


def _relative_to(root: Path, path: Path) -> str:
    return "." if path == root else str(path.relative_to(root))


# ── grid builders ─────────────────────────────────────────────────────────────
def policy_for(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """The **real** per-tool policy, override included.

    ``ToolRegistry.policy_for`` is the single source of truth: the table for the
    ordinary case, and a synthesised destructive policy when a write targets a
    blocked system prefix. Both shapes belong in the goldens.
    """
    return dict(tools.DEFAULT_TOOL_REGISTRY.policy_for(tool_name, args))


def policy_table() -> Dict[str, Any]:
    """The registry table, the default, and every args-dependent override."""
    default = dict(tools.DEFAULT_TOOL_REGISTRY.default_policy)
    table = {name: dict(policy) for name, policy in TOOL_GOVERNANCE.items()}
    overrides: Dict[str, Any] = {}
    for name in tool_universe():
        base = table.get(name, default)
        for variant, args in ARG_VARIANTS.items():
            policy = policy_for(name, args)
            if policy != base:
                overrides[f"{name}|{variant}"] = policy
    return {"schema": SCHEMA, "default": default, "tools": table, "overrides": overrides}


def policy_key(name: str, variant: str, default: Dict[str, Any]) -> str:
    """How a case names its policy: table entry, override, or the default."""
    base = TOOL_GOVERNANCE.get(name)
    policy = policy_for(name, ARG_VARIANTS[variant])
    if base is not None and policy == dict(base):
        return name
    if base is None and policy == default:
        return "@default"
    return f"{name}|{variant}"


def call_rows() -> List[Dict[str, Any]]:
    """The mode-*invariant* half of the grid: policy, breaker, classification.

    Circuit breakers and change classification do not read the mode — that is a
    documented property of the kernel, and keeping them in their own file is how
    the fixture states it rather than repeating it three times.
    """
    default = dict(tools.DEFAULT_TOOL_REGISTRY.default_policy)
    rows: List[Dict[str, Any]] = []
    for name in tool_universe():
        for variant, args in ARG_VARIANTS.items():
            policy = policy_for(name, args)
            rows.append({
                "tool": name,
                "variant": variant,
                "policy": policy_key(name, variant, default),
                "circuit_breaker": is_circuit_breaker(name, policy, args),
                "classification": classify_tool_call(
                    name, args, policy=policy, path_exists=lambda p: p in EXISTING_PATHS,
                ),
            })
    return rows


def decision_rows(mode: str) -> List[Dict[str, Any]]:
    """One mode's half: auto-approve, block reason, proposal staging."""
    rows: List[Dict[str, Any]] = []
    for name in tool_universe():
        for variant, args in ARG_VARIANTS.items():
            policy = policy_for(name, args)
            classification = classify_tool_call(
                name, args, policy=policy, path_exists=lambda p: p in EXISTING_PATHS,
            )
            rows.append({
                "tool": name,
                "variant": variant,
                "auto_approve": effective_auto_approve(mode, name, policy, args=args),
                "block_reason": block_reason_for_tool(mode, name, policy, args),
                "stage_proposal": should_stage_proposal(
                    mode, proposal_required=classification["proposal_required"],
                ),
            })
    return rows


def change_class_rows(mode: str) -> List[Dict[str, Any]]:
    """``effective_auto_approve``'s second axis, over the workspace writers."""
    rows: List[Dict[str, Any]] = []
    for name in sorted(WORKSPACE_WRITE_TOOLS | {"local_write", "read_file", "run_command"}):
        policy = policy_for(name, {})
        for change_class in CHANGE_CLASSES:
            rows.append({
                "tool": name,
                "change_class": change_class,
                "auto_approve": effective_auto_approve(
                    mode, name, policy, change_class=change_class,
                ),
            })
    return rows


def approval_rows(mode: str) -> List[Dict[str, Any]]:
    """Plan-level gates: which steps stay non-auto, and whether the plan pauses."""
    governance = {name: dict(policy) for name, policy in TOOL_GOVERNANCE.items()}
    rows: List[Dict[str, Any]] = []
    for case in PLAN_CASES:
        non_auto = non_auto_plan_steps(
            mode, case["steps"], governance, governed_tools=case["governed"],
        )
        rows.append({
            "key": case["key"],
            "non_auto_steps": non_auto,
            "requires_approval": plan_requires_approval(
                mode, non_auto_steps=non_auto, plan_flag=case["plan_flag"],
            ),
        })
    return rows


def normalize_rows() -> List[Dict[str, Any]]:
    return [
        {"input": value, "mode": normalize_mode(value).value}
        for value in NORMALIZE_INPUTS
    ]


def contract_payload() -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "default_mode": normalize_mode(None).value,
        "contracts": {mode: mode_contract(mode) for mode in MODES},
    }


def shlex_rows() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for command in SHLEX_CASES:
        try:
            rows.append({"input": command, "tokens": shlex.split(command)})
        except ValueError as exc:
            rows.append({"input": command, "error": str(exc)})
    return rows


def command_rows(root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    """Validation verdicts, plus the environment the validator would spawn into."""
    rows: List[Dict[str, Any]] = []
    spawn_env: Dict[str, Any] = {}
    with agent_root(root) as resolved, validation_only() as searched:
        for key, command, cwd in COMMAND_CASES:
            try:
                command_tools.run_command(command, cwd)
            except _Spawned as spawned:
                env = {k: v.replace(str(resolved), "<AGENT_ROOT>") for k, v in spawned.env.items()}
                if spawn_env and env != spawn_env:  # pragma: no cover - defensive
                    raise AssertionError("the sandbox environment is not constant")
                spawn_env = env
                rows.append({
                    "key": key, "command": command, "cwd": cwd, "outcome": "spawn",
                    "executable": Path(spawned.argv[0]).name,
                    "args": spawned.argv[1:],
                    "workdir": _relative_to(resolved, Path(str(spawned.cwd))),
                })
            except (tools.ToolError, ValueError) as exc:
                rows.append({
                    "key": key, "command": command, "cwd": cwd,
                    "outcome": "error", "error": _error(exc),
                })
            else:  # pragma: no cover - the shim always raises
                raise AssertionError(f"{command!r} reached the real subprocess")
    return rows, spawn_env, searched


def execution_rows(root: Path) -> List[Dict[str, Any]]:
    """The real ``run_command``, really executed, with the answers pinned."""
    rows: List[Dict[str, Any]] = []
    with agent_root(root) as resolved:
        for key, command, cwd, pin_stderr in EXECUTION_CASES:
            result = command_tools.run_command(command, cwd)
            row = {
                "key": key,
                "command": command,
                "cwd": cwd,
                "result_cwd": result["cwd"],
                "returncode": result["returncode"],
                "stdout": result["stdout"].replace(str(resolved), "<AGENT_ROOT>"),
            }
            if pin_stderr:
                row["stderr"] = result["stderr"]
            rows.append(row)
    return rows


def path_rows(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with agent_root(root) as resolved:
        for key, raw in PATH_CASES:
            candidate = raw.replace("<AGENT_ROOT>", str(resolved))
            try:
                resolved_path = tools.resolve_workspace_path(candidate)
            except tools.ToolError as exc:
                rows.append({"key": key, "input": raw, "outcome": "error",
                             "error": _error(exc)})
            else:
                rows.append({"key": key, "input": raw, "outcome": "ok",
                             "relative": _relative_to(resolved, resolved_path)})
    return rows


def constants() -> Dict[str, Any]:
    """Every table the Rust kernel duplicates, so drift is a failing assertion."""
    return {
        "max_file_bytes": tools.MAX_FILE_BYTES,
        "max_command_seconds": tools.MAX_COMMAND_SECONDS,
        "max_command_output": tools.MAX_COMMAND_OUTPUT,
        "safe_executable_path": command_tools._SAFE_EXECUTABLE_PATH,
        "allowed_commands": sorted(tools.ALLOWED_COMMANDS),
        "blocked_commands": sorted(tools.BLOCKED_COMMANDS),
        "allowed_git_subcommands": sorted(tools.ALLOWED_GIT_SUBCOMMANDS),
        "blocked_find_flags": sorted(command_tools._BLOCKED_FIND_FLAGS),
        "blocked_rg_flags": sorted(command_tools._BLOCKED_RG_FLAGS),
        "shell_operators": ["|", "&&", "||", ";", ">", "<", "$(", "`"],
        "hard_block_sandboxes": sorted(HARD_BLOCK_SANDBOXES),
        "knowledge_read_tools": sorted(KNOWLEDGE_READ_TOOLS),
        "workspace_write_tools": sorted(WORKSPACE_WRITE_TOOLS),
        "computer_observation_tools": sorted(COMPUTER_OBSERVATION_TOOLS),
        "computer_control_tools": sorted(COMPUTER_CONTROL_TOOLS),
        "mutating_tool_inventory": dict(sorted(MUTATING_TOOL_INVENTORY.items())),
        "proposal_capable_tools": sorted(PROPOSAL_CAPABLE_TOOLS),
    }


def manifest(searched_paths: List[str]) -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "modes": MODES,
        "tools": tool_universe(),
        "arg_variants": ARG_VARIANTS,
        "existing_paths": sorted(EXISTING_PATHS),
        "change_classes": CHANGE_CLASSES,
        "plans": PLAN_CASES,
        "tree": TREE,
        "pinned_env": PINNED_ENV,
        "constants": constants(),
        # Evidence that the allowlisted binary is looked up on the fixed PATH and
        # nowhere else: every which() the validator made, deduplicated.
        "which_paths": sorted(set(searched_paths)),
    }


# ── writing ───────────────────────────────────────────────────────────────────
def _dump(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _dump_grid(path: Path, header: Dict[str, Any], groups: Dict[str, List[Any]]) -> None:
    """Header pretty, cases one per line — a thousand-row diff stays readable."""
    parts = [
        f"  {json.dumps(key, ensure_ascii=False)}: "
        + json.dumps(header[key], ensure_ascii=False, sort_keys=True)
        for key in sorted(header)
    ]
    for name in sorted(groups):
        rows = ",\n    ".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) for row in groups[name]
        )
        body = f"[\n    {rows}\n  ]" if rows else "[]"
        parts.append(f"  {json.dumps(name)}: {body}")
    path.write_text("{\n" + ",\n".join(parts) + "\n}\n", encoding="utf-8")


def build(root: Path) -> Dict[str, Callable[[], None]]:
    """Every golden, keyed by filename, as a thunk that writes it.

    """
    # run_command left the worker (WP-P1). commands.json / execution.json
    # stay FROZEN at fc65e60; only rewrite them when the last generating
    # commands module is still importable (recovery / historical tree).
    if hasattr(command_tools, "run_command"):
        commands, spawn_env, searched = command_rows(root)
    else:
        commands, spawn_env, searched = None, None, [command_tools._SAFE_EXECUTABLE_PATH]
    return {
        "manifest.json": lambda: _dump(GOLDEN_DIR / "manifest.json", manifest(searched)),
        "policies.json": lambda: _dump(GOLDEN_DIR / "policies.json", policy_table()),
        "contract.json": lambda: _dump(GOLDEN_DIR / "contract.json", contract_payload()),
        "calls.json": lambda: _dump_grid(
            GOLDEN_DIR / "calls.json", {"schema": SCHEMA}, {"cases": call_rows()},
        ),
        "normalize.json": lambda: _dump_grid(
            GOLDEN_DIR / "normalize.json", {"schema": SCHEMA}, {"cases": normalize_rows()},
        ),
        "shlex.json": lambda: _dump_grid(
            GOLDEN_DIR / "shlex.json", {"schema": SCHEMA}, {"cases": shlex_rows()},
        ),
        "commands.json": (
            (lambda: _dump_grid(
                GOLDEN_DIR / "commands.json",
                {"schema": SCHEMA, "spawn_env": spawn_env},
                {"cases": commands},
            ))
            if commands is not None
            else (lambda: None)
        ),
        "execution.json": (
            (lambda: _dump_grid(
                GOLDEN_DIR / "execution.json", {"schema": SCHEMA},
                {"cases": execution_rows(root)},
            ))
            if commands is not None
            else (lambda: None)
        ),
        "paths.json": lambda: _dump_grid(
            GOLDEN_DIR / "paths.json", {"schema": SCHEMA}, {"cases": path_rows(root)},
        ),
        **{
            f"decisions__{mode}.json": (lambda mode=mode: _dump_grid(
                GOLDEN_DIR / f"decisions__{mode}.json",
                {"schema": SCHEMA, "mode": mode},
                {
                    "cases": decision_rows(mode),
                    "change_class_cases": change_class_rows(mode),
                    "plan_cases": approval_rows(mode),
                },
            ))
            for mode in MODES
        },
    }


def main() -> int:
    # Never rmtree: commands.json / execution.json are FROZEN at fc65e60
    # once run_command leaves the worker. Write other goldens in place.
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with pinned_environment(), tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "agent_workspace"
        build_tree(root)
        writers = build(root)
        for name in sorted(writers):
            writers[name]()
    total = sum(path.stat().st_size for path in GOLDEN_DIR.glob("*.json"))
    print(f"golden: {len(list(GOLDEN_DIR.glob('*.json')))} files, {total / 1024:.1f} KiB")
    print(f"grid: {len(tool_universe())} tools × {len(ARG_VARIANTS)} variants × {len(MODES)} modes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
