"""One answer to "which workspace is this request talking about?".

Eight routers used to each re-derive this from raw headers. They did not agree:
some accepted the ``workspace_id`` query parameter and some only the
``X-Workspace-Id`` header, and only three of them checked that a body's
``workspace_id`` matched the header instead of silently letting one win. A
guard that exists in some handlers and not others is not a guard — this module
is the single implementation they all call, so the rule is stated once and
holds everywhere.

The rule:

* A caller may name a workspace in the ``X-Workspace-Id`` header, the
  ``workspace_id`` query parameter, or the request body.
* If more than one of those is present they must **agree**. Disagreement is a
  ``403``, never a silent preference — a request that names two workspaces has
  no single meaning, and picking one is how a scoped write lands in the wrong
  vault.
* Naming nothing resolves to ``None``, which every caller passes to
  :class:`~latticeai.services.workspace_service.WorkspaceService`, where it
  falls back to the active workspace (Personal by default). That is the
  pre-1.1 single-workspace behaviour and it stays intact.

Permission is *not* decided here. ``WorkspaceService`` owns read/write gating;
this module only decides what was asked for and translates the service's
``PermissionError`` into the HTTP boundary's ``403``.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import HTTPException, Request

__all__ = [
    "WORKSPACE_HEADER",
    "WORKSPACE_PARAM",
    "requested_workspace",
    "resolve_workspace_scope",
    "workspace_scope_from_request",
]

WORKSPACE_HEADER = "X-Workspace-Id"
WORKSPACE_PARAM = "workspace_id"

_MISMATCH_DETAIL = "Workspace selectors must match."


def _clean(value: Any) -> Optional[str]:
    """Normalize one selector to a non-empty string, or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def workspace_scope_from_request(request: Request) -> Optional[str]:
    """Resolve the workspace named by header/query, or ``None``.

    ``None`` lets the service fall back to the active workspace (Personal by
    default), preserving pre-1.1 behaviour for clients that send no header.
    """
    header = _clean(request.headers.get(WORKSPACE_HEADER))
    if header:
        return header
    return _clean(request.query_params.get(WORKSPACE_PARAM))


def requested_workspace(
    request: Request,
    *,
    body_workspace: Any = None,
) -> Optional[str]:
    """The one workspace this request names, or ``None``.

    Raises ``403`` when the header, query parameter, and body disagree.
    """
    selectors: List[str] = []
    for value in (
        _clean(body_workspace),
        _clean(request.headers.get(WORKSPACE_HEADER)),
        _clean(request.query_params.get(WORKSPACE_PARAM)),
    ):
        if value is not None:
            selectors.append(value)
    if len(set(selectors)) > 1:
        raise HTTPException(status_code=403, detail=_MISMATCH_DETAIL)
    return selectors[0] if selectors else None


def resolve_workspace_scope(
    request: Request,
    *,
    user: Optional[str],
    workspace_service: Any = None,
    write: bool = True,
    body_workspace: Any = None,
    allow_unscoped_anonymous: bool = False,
) -> Optional[str]:
    """Resolve *and authorize* the workspace a handler should act on.

    ``workspace_service=None`` is the standalone/embedded router contract: the
    named workspace passes through ungated, exactly as each local copy of this
    logic did. With a service present, reads are gated on ``read`` and writes
    on ``write``, and a denial surfaces as ``403``.

    ``allow_unscoped_anonymous`` preserves one deliberate exception: a no-auth
    local caller that names no workspace keeps its legacy *unscoped* records
    instead of being resolved onto the active workspace.
    """
    requested = requested_workspace(request, body_workspace=body_workspace)
    if workspace_service is None:
        return requested
    if allow_unscoped_anonymous and not user and requested is None:
        return None
    resolver = (
        workspace_service.resolve_write_scope
        if write
        else workspace_service.resolve_read_scope
    )
    try:
        scope: Optional[str] = resolver(requested, user or None)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return scope
