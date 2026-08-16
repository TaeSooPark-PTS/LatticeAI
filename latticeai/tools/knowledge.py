"""Knowledge-base / Obsidian vault tools over the local brain directory."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from latticeai.core.quiet import quiet
from latticeai.services.p_reinforce import BRAIN_DIR, STRUCTURE
from latticeai.tools import ToolError


def _scope_digest(kind: str, value: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{value}".encode("utf-8")).hexdigest()[:24]
    return f"{kind}-{digest}"


def knowledge_scope_root(
    *,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Path:
    """Resolve a private vault partition for an authenticated workspace user.

    Calls without either value preserve the historical single-user/local vault
    API. Authenticated tool surfaces always provide both values. A half-scoped
    request fails closed so a missing identity can never fall back to the
    shared legacy vault.
    """
    workspace = str(workspace_id or "").strip()
    user = str(user_email or "").strip().lower()
    if not workspace and not user:
        return BRAIN_DIR
    if not workspace or not user:
        raise ToolError("Knowledge tools require both workspace_id and user_email.")
    return (
        BRAIN_DIR
        / ".lattice-scopes"
        / _scope_digest("workspace", workspace)
        / _scope_digest("user", user)
    )


def knowledge_search(
    query: str,
    max_results: int = 5,
    *,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Dict[str, Any]:
    if not query:
        raise ToolError("Query is required.")
    max_results = max(1, min(int(max_results), 20))
    query_lower = query.lower()
    results: List[Dict[str, Any]] = []
    root = knowledge_scope_root(workspace_id=workspace_id, user_email=user_email)

    for file_path in root.rglob("*.md"):
        if len(results) >= max_results:
            break
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            quiet()
            continue
        if query_lower in content.lower() or query_lower in file_path.name.lower():
            results.append(
                {
                    "path": str(file_path),
                    "relative_path": str(file_path.relative_to(root)),
                    "preview": content[:500],
                }
            )

    return {"query": query, "results": results}


def knowledge_tree(
    *,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    scope_root = knowledge_scope_root(workspace_id=workspace_id, user_email=user_email)
    for folder in STRUCTURE:
        root = scope_root / folder
        root.mkdir(parents=True, exist_ok=True)
        for file_path in sorted(root.rglob("*.md")):
            entries.append(
                {
                    "folder": folder,
                    "relative_path": str(file_path.relative_to(scope_root)),
                    "size": file_path.stat().st_size,
                }
            )
    return {"root": str(scope_root), "entries": entries}


def obsidian_search(
    query: str,
    max_results: int = 5,
    *,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Dict[str, Any]:
    root = knowledge_scope_root(workspace_id=workspace_id, user_email=user_email)
    result = knowledge_search(
        query,
        max_results,
        workspace_id=workspace_id,
        user_email=user_email,
    )
    result["vault_root"] = str(root)
    return result


def obsidian_tree(
    *,
    workspace_id: Optional[str] = None,
    user_email: Optional[str] = None,
) -> Dict[str, Any]:
    return knowledge_tree(workspace_id=workspace_id, user_email=user_email)
