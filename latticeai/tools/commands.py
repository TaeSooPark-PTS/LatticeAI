"""Command/build/deploy and read-only git tools (no shell, allow-listed)."""

from __future__ import annotations

import subprocess
from typing import Any, Dict, List, Optional

import latticeai.tools as tools
from latticeai.tools import (
    ALLOWED_GIT_SUBCOMMANDS,
    MAX_COMMAND_OUTPUT,
    MAX_COMMAND_SECONDS,
    ToolError,
    _relative,
    _resolve_path,
)

# find(1) flags that execute or delete; checked in run_command.
_BLOCKED_FIND_FLAGS = {
    "-delete",
    "-exec",
    "-execdir",
    "-fls",
    "-fprint",
    "-fprintf",
    "-ok",
    "-okdir",
}
_BLOCKED_RG_FLAGS = {"--pre", "--pre-glob"}
_SAFE_EXECUTABLE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"


def _run_git(args: List[str], cwd: Optional[str] = None) -> Dict[str, Any]:
    if not args:
        raise ToolError("Git subcommand is required.")
    subcommand = args[0]
    if subcommand not in ALLOWED_GIT_SUBCOMMANDS:
        raise ToolError(f"Git subcommand is not allowed: {subcommand}")
    if any(arg.startswith(("git@", "http://", "https://", "ssh://")) for arg in args):
        raise ToolError("Remote git targets are not allowed.")

    workdir = _resolve_path(cwd or ".")
    if not workdir.exists() or not workdir.is_dir():
        raise ToolError("Working directory does not exist.")

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=MAX_COMMAND_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"Git command timed out after {MAX_COMMAND_SECONDS} seconds.")

    return {
        "command": "git " + " ".join(args),
        "cwd": _relative(workdir) if workdir != tools.AGENT_ROOT else ".",
        "returncode": completed.returncode,
        "stdout": completed.stdout[-MAX_COMMAND_OUTPUT:],
        "stderr": completed.stderr[-MAX_COMMAND_OUTPUT:],
    }


def git_status(cwd: Optional[str] = None) -> Dict[str, Any]:
    return _run_git(["status", "--short"], cwd)


def git_diff(path: Optional[str] = None, cwd: Optional[str] = None) -> Dict[str, Any]:
    args = ["diff", "--"]
    if path:
        target = _resolve_path(path)
        args.append(_relative(target))
    return _run_git(args, cwd)


def git_log(max_count: int = 5, cwd: Optional[str] = None) -> Dict[str, Any]:
    max_count = max(1, min(int(max_count), 20))
    return _run_git(["log", f"--max-count={max_count}", "--oneline", "--decorate"], cwd)


def git_show(revision: str = "HEAD", cwd: Optional[str] = None) -> Dict[str, Any]:
    if revision.startswith("-") or any(token in revision for token in ["..", ":", "/", "\\"]):
        raise ToolError("Revision is not allowed.")
    return _run_git(["show", "--stat", "--oneline", "--decorate", revision], cwd)
