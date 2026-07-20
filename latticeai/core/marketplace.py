"""Marketplace foundation for local templates.

v2.1 intentionally does not add a cloud marketplace. This module provides the
portable template shape, metadata, export/import validation, and install hooks
that a future service can reuse.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional


MARKETPLACE_VERSION = "9.8.0"
TEMPLATE_KINDS = ("plugin", "workflow", "agent", "ingestion_bridge")


def _agent_template(
    template_id: str,
    name: str,
    description: str,
    *,
    roles: List[str],
    capabilities: List[str],
    suggested_tools: List[str],
    category: str,
    max_retries: int = 2,
) -> Dict[str, Any]:
    """Build a portable agent template entry (Part 4 reusable templates)."""
    return {
        "id": template_id,
        "kind": "agent",
        "name": name,
        "version": "1.0.0",
        "description": description,
        "metadata": {"category": category, "installable": True, "agent_template": True},
        "definition": {
            "roles": roles,
            "max_retries": max_retries,
            "capabilities": capabilities,
            "suggested_tools": suggested_tools,
            "constraints": ["workspace scoped", "no secret leakage", "replayable timeline"],
        },
    }


class MarketplaceError(Exception):
    """Raised for invalid template operations."""


BUILTIN_TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "plugin": [
        {
            "id": "plugin-review-action",
            "kind": "plugin",
            "name": "Plugin Review Action",
            "version": "1.0.0",
            "description": "A permissioned plugin action template for review workflows.",
            "metadata": {"category": "review", "installable": True},
            "files": {
                "plugin.json": {
                    "id": "plugin-review-action",
                    "name": "Plugin Review Action",
                    "version": "1.0.0",
                    "lattice_version": ">=2.2.0",
                    "permissions": ["read_workspace", "run_skills"],
                    "provides": {"skills": ["review_action"]},
                }
            },
        }
    ],
    "workflow": [
        {
            "id": "workflow-agent-plugin-review",
            "kind": "workflow",
            "name": "Agent Plugin Review Workflow",
            "version": "1.0.0",
            "description": "Manual trigger into planner/executor/reviewer, plugin action, and output.",
            "metadata": {"category": "agent-ops", "installable": True},
            "definition": {
                "name": "Agent Plugin Review Workflow",
                "nodes": [
                    {"id": "trigger", "type": "trigger", "name": "Manual start", "config": {"trigger": "manual"}, "next": "agent"},
                    {"id": "agent", "type": "agent", "name": "Plan and execute", "config": {"goal": "Run agent platform review", "roles": ["planner", "executor", "reviewer"]}, "next": "plugin"},
                    {"id": "plugin", "type": "plugin", "name": "Plugin action", "config": {"plugin": "hello-world", "action": "run_skill", "args": {}}, "next": "output"},
                    {"id": "output", "type": "output", "name": "Output", "config": {}, "next": None},
                ],
                "metadata": {"template_id": "workflow-agent-plugin-review"},
            },
        }
    ],
    "agent": [
        {
            "id": "agent-planner-executor-reviewer",
            "kind": "agent",
            "name": "Planner Executor Reviewer",
            "version": "1.0.0",
            "description": "Default bounded planning, execution, review, and retry template.",
            "metadata": {"category": "agent-ops", "installable": True},
            "definition": {
                "roles": ["planner", "executor", "reviewer"],
                "max_retries": 2,
                "constraints": ["workspace scoped", "no secret leakage", "replayable timeline"],
            },
        },
        _agent_template(
            "agent-research-assistant", "Research Assistant",
            "Retrieves workspace context, plans an inquiry, and synthesizes a reviewed answer.",
            roles=["researcher", "planner", "reviewer"],
            capabilities=["context-retrieval", "hybrid-search", "memory-recall", "synthesis"],
            suggested_tools=["knowledge_search", "knowledge_graph_search", "read_file"],
            category="research",
        ),
        _agent_template(
            "agent-coding-assistant", "Coding Assistant",
            "Plans a change, edits files, runs the build, and reviews the result before finishing.",
            roles=["planner", "executor", "reviewer"],
            capabilities=["task-decomposition", "tool-use", "file-write", "verification"],
            suggested_tools=["edit_file", "write_file", "run_command", "build_project", "git_diff"],
            category="coding",
        ),
        _agent_template(
            "agent-knowledge-curator", "Knowledge Curator",
            "Captures, structures, and saves knowledge into the graph and memory.",
            roles=["researcher", "executor"],
            capabilities=["context-retrieval", "graph-read", "knowledge-save"],
            suggested_tools=["knowledge_save", "knowledge_graph_ingest", "knowledge_tree"],
            category="knowledge",
        ),
        _agent_template(
            "agent-documentation-writer", "Documentation Writer",
            "Plans a document, drafts and writes it, and reviews for completeness.",
            roles=["planner", "executor", "reviewer"],
            capabilities=["task-decomposition", "file-write", "summarize", "verification"],
            suggested_tools=["create_docx", "create_pdf", "write_file", "read_document"],
            category="documentation",
        ),
        _agent_template(
            "agent-workflow-builder", "Workflow Builder",
            "Plans and assembles a multi-step workflow definition for repeatable automation.",
            roles=["planner", "executor"],
            capabilities=["task-decomposition", "workflow-run", "delegation"],
            suggested_tools=["todo_write", "workspace_tree"],
            category="automation",
        ),
    ],
    "ingestion_bridge": [
        {
            "id": "bridge-obsidian-markdown",
            "kind": "ingestion_bridge",
            "name": "Obsidian Markdown Bridge",
            "version": "1.0.0",
            "description": "Import a local Markdown vault through the unified ingestion pipeline.",
            "metadata": {"category": "interop", "installable": True},
            "definition": {
                "source_types": ["local_file", "markdown"],
                "file_patterns": ["*.md"],
                "pipeline": "unified-ingestion",
                "provenance": True,
                "graph_edges": ["indexed_from", "mentions"],
            },
        },
        {
            "id": "bridge-calendar-notes",
            "kind": "ingestion_bridge",
            "name": "Calendar Notes Bridge",
            "version": "1.0.0",
            "description": "Normalize calendar meeting notes into Brain events and source-linked notes.",
            "metadata": {"category": "interop", "installable": True},
            "definition": {
                "source_types": ["workspace_event", "note"],
                "pipeline": "unified-ingestion",
                "provenance": True,
                "graph_edges": ["indexed_from", "mentions"],
            },
        },
    ],
}


def _normalize_kind(kind: str) -> str:
    value = str(kind or "").strip().lower()
    if value not in TEMPLATE_KINDS:
        raise MarketplaceError(f"unknown template kind: {kind}")
    return value


class TemplateCatalog:
    """Local template catalog with export/import/install primitives."""

    def __init__(self, templates: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        self.templates = templates or BUILTIN_TEMPLATES

    def list_templates(self, kind: Optional[str] = None) -> Dict[str, Any]:
        kinds = [_normalize_kind(kind)] if kind else list(TEMPLATE_KINDS)
        templates = []
        for item_kind in kinds:
            templates.extend(deepcopy(self.templates.get(item_kind, [])))
        return {
            "marketplace_version": MARKETPLACE_VERSION,
            "kinds": list(TEMPLATE_KINDS),
            "templates": templates,
            "total": len(templates),
        }

    def get_template(self, kind: str, template_id: str) -> Dict[str, Any]:
        item_kind = _normalize_kind(kind)
        for template in self.templates.get(item_kind, []):
            if template.get("id") == template_id:
                return deepcopy(template)
        raise MarketplaceError(f"template not found: {item_kind}/{template_id}")

    def clone_template(self, kind: str, template_id: str, new_name: Optional[str] = None) -> Dict[str, Any]:
        """Return an editable copy of a template with a fresh id (Part 4 clone)."""
        template = self.get_template(kind, template_id)
        name = (new_name or f"{template['name']} (Copy)").strip()
        slug = name.lower().replace(" ", "-").replace("(", "").replace(")", "")
        clone = deepcopy(template)
        clone["id"] = f"{template['id']}-copy-{slug}"[:80]
        clone["name"] = name
        clone["version"] = "1.0.0"
        clone["metadata"] = {
            **(template.get("metadata") or {}),
            "cloned_from": template_id,
            "editable": True,
        }
        return clone

    def export_template(self, kind: str, template_id: str) -> Dict[str, Any]:
        template = self.get_template(kind, template_id)
        return {
            "lattice_template_export": MARKETPLACE_VERSION,
            "kind": template["kind"],
            "template": template,
            "metadata": {
                "exported_from": "local",
                "template_id": template_id,
                "template_version": template.get("version"),
            },
        }

    def import_template(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise MarketplaceError("template import payload must be an object")
        template = deepcopy(payload.get("template") or payload)
        kind = _normalize_kind(template.get("kind") or payload.get("kind"))
        if not template.get("id"):
            raise MarketplaceError("template missing id")
        if not template.get("name"):
            raise MarketplaceError("template missing name")
        template["kind"] = kind
        template.setdefault("version", "1.0.0")
        template.setdefault("metadata", {})
        template["metadata"] = {**template["metadata"], "imported": True}
        return template

    def install_template(
        self,
        template: Dict[str, Any],
        *,
        store: Any,
        user_email: Optional[str] = None,
        workspace_id: Optional[str] = None,
        graph: Any = None,
    ) -> Dict[str, Any]:
        imported = self.import_template(template)
        kind = imported["kind"]
        installed: Dict[str, Any] = {
            "kind": kind,
            "template_id": imported["id"],
            "name": imported["name"],
            "version": imported.get("version", "1.0.0"),
        }
        if kind == "workflow":
            definition = imported.get("definition") or {}
            workflow = store.create_workflow(
                name=definition.get("name") or imported["name"],
                steps=[{"action": node.get("type"), "node": node.get("id")} for node in definition.get("nodes", [])],
                nodes=definition.get("nodes", []),
                metadata={**(definition.get("metadata") or {}), "template_id": imported["id"]},
                user_email=user_email,
                graph=graph,
                workspace_id=workspace_id,
            )
            installed["workflow_id"] = workflow["id"]
        registry = store.mark_template_installed(
            kind=kind,
            template_id=imported["id"],
            version=imported.get("version", "1.0.0"),
            metadata=imported.get("metadata") or {},
            workspace_id=workspace_id,
        )
        installed["registry"] = registry
        return installed
