"""Command/build/deploy and read-only git tools (no shell, allow-listed)."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import tools
from tools import (
    ToolError,
    ensure_agent_root,
    _resolve_path,
    _relative,
    ALLOWED_COMMANDS,
    BLOCKED_COMMANDS,
    BUILD_SCRIPT_NAMES,
    DEPLOY_SCRIPT_NAMES,
    ALLOWED_GIT_SUBCOMMANDS,
    MAX_COMMAND_SECONDS,
    MAX_BUILD_SECONDS,
    MAX_DEPLOY_SECONDS,
    MAX_COMMAND_OUTPUT,
)

# find(1) flags that execute or delete; checked in run_command.
_BLOCKED_FIND_FLAGS = {"-exec", "-execdir", "-delete", "-ok", "-okdir"}


def run_command(command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
    ensure_agent_root()
    parts = shlex.split(command)
    if not parts:
        raise ToolError("Command is empty.")

    executable = Path(parts[0]).name
    if executable in BLOCKED_COMMANDS or executable not in ALLOWED_COMMANDS:
        raise ToolError(f"Command is not allowed: {executable}")
    if executable == "git":
        raise ToolError("Use the read-only git_status, git_diff, git_log, or git_show tools.")
    if any(token in command for token in ["|", "&&", "||", ";", ">", "<", "$(", "`"]):
        raise ToolError("Shell operators are not allowed.")
    if executable == "find":
        blocked = [f for f in parts[1:] if f in _BLOCKED_FIND_FLAGS]
        if blocked:
            raise ToolError(f"find flags are not allowed: {', '.join(blocked)}")
    abs_args = [a for a in parts[1:] if a.startswith("/") and a not in ("/dev/null",)]
    if abs_args:
        raise ToolError(f"Absolute paths in command arguments are not allowed: {abs_args[0]}")

    workdir = _resolve_path(cwd or ".")
    if not workdir.exists() or not workdir.is_dir():
        raise ToolError("Working directory does not exist.")

    try:
        completed = subprocess.run(
            parts,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=MAX_COMMAND_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"Command timed out after {MAX_COMMAND_SECONDS} seconds.")

    stdout = completed.stdout[-MAX_COMMAND_OUTPUT:]
    stderr = completed.stderr[-MAX_COMMAND_OUTPUT:]
    return {
        "command": command,
        "cwd": _relative(workdir) if workdir != tools.AGENT_ROOT else ".",
        "returncode": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _load_package_scripts(workdir: Path) -> Dict[str, str]:
    package_json = workdir / "package.json"
    if not package_json.exists():
        return {}
    try:
        import json
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ToolError(f"Could not parse package.json: {exc}") from exc
    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict):
        return {}
    return {str(key): str(value) for key, value in scripts.items()}


def _run_script(script: str, cwd: Optional[str], allowed: set[str], timeout: int) -> Dict[str, Any]:
    ensure_agent_root()
    if script not in allowed:
        raise ToolError(f"Script is not allowed here: {script}")
    workdir = _resolve_path(cwd or ".")
    if not workdir.exists() or not workdir.is_dir():
        raise ToolError("Working directory does not exist.")

    scripts = _load_package_scripts(workdir)
    if script not in scripts:
        raise ToolError(f"package.json does not define a '{script}' script.")

    try:
        completed = subprocess.run(
            ["npm", "run", script],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"npm run {script} timed out after {timeout} seconds.")

    return {
        "command": f"npm run {script}",
        "cwd": _relative(workdir) if workdir != tools.AGENT_ROOT else ".",
        "script_body": scripts[script],
        "returncode": completed.returncode,
        "stdout": completed.stdout[-MAX_COMMAND_OUTPUT:],
        "stderr": completed.stderr[-MAX_COMMAND_OUTPUT:],
    }


def build_project(cwd: Optional[str] = None, script: str = "build") -> Dict[str, Any]:
    return _run_script(script, cwd, BUILD_SCRIPT_NAMES, MAX_BUILD_SECONDS)


def deploy_project(cwd: Optional[str] = None, script: str = "deploy") -> Dict[str, Any]:
    return _run_script(script, cwd, DEPLOY_SCRIPT_NAMES, MAX_DEPLOY_SECONDS)


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
