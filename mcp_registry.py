"""Deprecation shim — the MCP registry moved to ``latticeai.core.mcp_registry`` in v4.

This root module remains importable for the deprecation window and will be
removed in a future major release. Import from ``latticeai.core.mcp_registry``.

Note: the remote-registry cache lives in ``latticeai.core.mcp_registry``
module globals — code that *assigns* cache attributes (e.g.
``_REMOTE_REGISTRY_FETCHED_AT``) must import the real module, not this shim.
"""

from latticeai.core.mcp_registry import *  # noqa: F401,F403
from latticeai.core.mcp_registry import (  # noqa: F401 — explicit key surface
    MCP_REGISTRY,
    SKILLS_DIR,
    _KNOWN_REPO_LICENSES,
    _MARKETPLACE_API,
    _MARKETPLACE_RAW,
    _THIRD_PARTY_SKILL_SOURCES,
    _extract_skill_desc,
    _fetch_plugin_directory,
    _fetch_plugin_skills,
    _fetch_remote_mcp_registry,
    _fetch_skills_marketplace,
    _get_combined_registry,
    install_skill,
)

__all__ = ["MCP_REGISTRY", "SKILLS_DIR", "install_skill"]
