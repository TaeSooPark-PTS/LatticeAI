"""Workspace OS vocabulary: types, roles, permissions, areas, defaults.

Split out of ``workspace_os.py`` in 10.2.0 so the store module holds behaviour
and this one holds the vocabulary that behaviour is written against. Every name
is re-exported from ``workspace_os`` — the import path callers use has not
changed.
"""

from __future__ import annotations

from typing import Dict

WORKSPACE_OS_VERSION = "11.4.0"

# Workspace types separate single-user Personal workspaces from shared
# Organization workspaces. Both keep the same local-first JSON store; the type
# only changes how membership and permissions are evaluated.
WORKSPACE_TYPES = ("personal", "organization")

DEFAULT_WORKSPACE_ID = "personal"

# Role hierarchy for Organization workspaces. Personal workspaces always grant
# their single local user the owner role.
WORKSPACE_ROLES = ("owner", "admin", "member", "viewer")

# Capability-style permissions. Kept intentionally small so Enterprise editions
# can layer advanced RBAC/ABAC on top via the enterprise seam without changing
# these community defaults.
WORKSPACE_PERMISSIONS = ("read", "write", "manage_members", "manage_workspace")

ROLE_PERMISSIONS: Dict[str, set] = {
    "owner": {"read", "write", "manage_members", "manage_workspace"},
    "admin": {"read", "write", "manage_members", "manage_workspace"},
    "member": {"read", "write"},
    "viewer": {"read"},
}

WORKSPACE_AREAS = [
    "graph",
    "snapshot",
    "memory",
    "agent",
    "workflow",
    "plugins",
    "skills",
    "marketplace",
    "timeline",
]

ONBOARDING_STEPS = [
    "account",
    "admin",
    "hardware",
    "model_recommendation",
    "model_install",
    "model_connection",
    "folder_connection",
    "first_question",
    "complete",
]

MEMORY_KINDS = {
    "short_term",
    "workspace",
    "preferences",
    "decisions",
    "working_style",
    "frequently_used_tools",
    "long_term",
}

EXECUTION_EVENT_TYPES = {
    "agent_started",
    "handoff_created",
    "handoff_accepted",
    "handoff_completed",
    "review_requested",
    "review_approved",
    "review_rejected",
    "retry_requested",
    "workflow_started",
    "workflow_completed",
    "plugin_started",
    "plugin_completed",
    "execution_failed",
    "execution_cancelled",
    "execution_interrupted",
}

DEFAULT_AGENTS = [
    {
        "id": "agent:planner",
        "name": "Planner",
        "role": "Breaks workspace goals into executable plans.",
        "status": "available",
        "relationships": ["agent:executor", "agent:reviewer"],
    },
    {
        "id": "agent:executor",
        "name": "Executor",
        "role": "Runs approved tool and code workflows.",
        "status": "available",
        "relationships": ["agent:planner", "agent:reviewer"],
    },
    {
        "id": "agent:reviewer",
        "name": "Reviewer",
        "role": "Checks outputs, tests, and regressions.",
        "status": "available",
        "relationships": ["agent:executor", "agent:release"],
    },
    {
        "id": "agent:researcher",
        "name": "Researcher",
        "role": "Finds and curates relevant workspace knowledge.",
        "status": "available",
        "relationships": ["agent:planner"],
    },
    {
        "id": "agent:release",
        "name": "Release Agent",
        "role": "Coordinates versioning, packaging, and release checks.",
        "status": "available",
        "relationships": ["agent:reviewer"],
    },
]
