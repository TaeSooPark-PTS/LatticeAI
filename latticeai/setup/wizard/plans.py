"""SSE framing and the confirmation-token plans for wizard install actions.

Every install action the wizard offers is turned into an explicit command plan
before it is shown, so the token the browser sends back can be compared against
the exact commands that would run. An action with nothing to execute has no
plan and therefore nothing to confirm.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from latticeai.core.sse import sse_frame
from latticeai.services.process_audit import command_plan_for_commands


def _sse(data: Dict) -> str:
    return sse_frame(None, data)


def _action_commands(action: Dict[str, Any]) -> List[List[str]]:
    atype = action.get("type")
    if atype == "pip":
        return [[sys.executable, "-m", "pip", "install", "--upgrade", str(pkg)] for pkg in action.get("packages", [])]
    if atype == "brew":
        package = str(action.get("package") or "")
        return [["brew", "install", package]] if package else []
    return []


def _action_command_plan(action: Dict[str, Any], *, name: str) -> Dict[str, Any] | None:
    commands = _action_commands(action)
    if not commands:
        return None
    return command_plan_for_commands(
        commands,
        name=name,
        purpose="setup_wizard_install",
        metadata={"action_type": action.get("type")},
    )


def _attach_action_plan(action: Dict[str, Any] | None, *, name: str) -> Dict[str, Any] | None:
    if not isinstance(action, dict):
        return action
    plan = _action_command_plan(action, name=name)
    if not plan:
        return action
    hydrated = dict(action)
    hydrated["command_plan"] = plan
    hydrated["confirmation_token"] = plan["confirmation_token"]
    return hydrated


def _hydrate_install_actions(groups: Dict[str, Any]) -> Dict[str, Any]:
    for group_name in ("components", "engines", "models", "mcps"):
        items = groups.get(group_name)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                item["action"] = _attach_action_plan(
                    item.get("action"),
                    name=str(item.get("id") or item.get("name") or group_name),
                )
    return groups


def _verify_action_confirmation(action: Dict[str, Any], token: str | None, *, name: str) -> bool:
    plan = _action_command_plan(action, name=name)
    if not plan:
        return True
    return str(token or "").strip() == plan["confirmation_token"]
