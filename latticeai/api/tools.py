"""Direct tool, upload, MCP, and knowledge utility routes."""

from __future__ import annotations

import base64
import inspect
import io
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from latticeai.api.computer_use import create_computer_use_router
from latticeai.api.local_files import create_local_files_router
from lattice_brain.runtime.hooks import dispatch_tool
from latticeai.api.mcp import create_mcp_router
from latticeai.api.permissions import create_permissions_router
from latticeai.services.upload_service import process_uploaded_document
from latticeai.services.tool_dispatch import (
    TOOL_GOVERNANCE,
    TOOL_GOVERNANCE_DEFAULT as _TOOL_GOVERNANCE_DEFAULT,
    check_tool_role as _check_tool_role,
    get_tool_permission,
    enforce_tool_policy,
    list_tool_permissions,
    tool_registry_diagnostics,
    tool_registry_manifest,
)
from latticeai.services.router_context import ToolRouterContext
from latticeai.tools import (
    AGENT_ROOT,
    ToolError,
    build_project,
    create_docx,
    create_pdf,
    create_pptx,
    create_xlsx,
    read_document,
    deploy_project,
    edit_file,
    git_diff,
    git_log,
    git_show,
    git_status,
    grep,
    inspect_html,
    knowledge_save,
    knowledge_search,
    knowledge_scope_root,
    knowledge_tree,
    list_dir,
    network_status,
    obsidian_save,
    obsidian_search,
    obsidian_tree,
    preview_url,
    read_file,
    run_command,
    search_files,
    todo_read,
    todo_write,
    workspace_tree,
    write_file,
)


class ToolPathRequest(BaseModel):
    path: str = "."
    approval_token: Optional[str] = None


class ToolWriteFileRequest(BaseModel):
    path: str
    content: str


class ToolRunCommandRequest(BaseModel):
    command: str
    cwd: Optional[str] = "."


class ToolScriptRequest(BaseModel):
    cwd: Optional[str] = "."
    script: str = "build"


class ToolSearchFilesRequest(BaseModel):
    query: str
    path: str = "."
    max_results: int = 20


class ToolReadFileRequest(BaseModel):
    path: str
    offset: int = 0
    limit: int = 0
    line_numbers: bool = True


class ToolEditFileRequest(BaseModel):
    path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class ToolGrepRequest(BaseModel):
    pattern: str
    path: str = "."
    glob: Optional[str] = None
    max_results: int = 50
    case_insensitive: bool = False
    context_lines: int = 0


class ToolTodoWriteRequest(BaseModel):
    todos: List[Dict] = []


class ToolWorkspaceTreeRequest(BaseModel):
    path: str = "."
    max_depth: int = 3


class ToolClearHistoryRequest(BaseModel):
    keep_last: int = 0


class ToolKnowledgeSaveRequest(BaseModel):
    content: str
    folder: str = "00_Raw"
    title: Optional[str] = None


class ToolKnowledgeSearchRequest(BaseModel):
    query: str
    max_results: int = 5


class ToolDocxRequest(BaseModel):
    title: str = ""
    body: str = ""
    filename: str = "document.docx"


class ToolXlsxRequest(BaseModel):
    rows: List[List] = []
    filename: str = "spreadsheet.xlsx"
    sheet_name: str = "Sheet1"


class ToolPptxRequest(BaseModel):
    title: str = ""
    slides: List[Dict] = []
    filename: str = "presentation.pptx"


class ToolPdfRequest(BaseModel):
    title: str = ""
    body: str = ""
    filename: str = "document.pdf"


class ToolGitDiffRequest(BaseModel):
    path: Optional[str] = None
    cwd: Optional[str] = "."


class ToolGitLogRequest(BaseModel):
    max_count: int = 5
    cwd: Optional[str] = "."


class ToolGitShowRequest(BaseModel):
    revision: str = "HEAD"
    cwd: Optional[str] = "."


def create_tools_router(
    *,
    tool_context: ToolRouterContext | None = None,
    config=None,
    ingestion_pipeline=None,
    data_dir: Path | None = None,
    static_dir: Path | None = None,
    model_router=None,
    require_user=None,
    require_admin=None,
    get_current_user=None,
    clear_history=None,
    append_audit_event=None,
    enforce_rate_limit=None,
    bytes_match_extension=None,
    classify_sensitive_message=None,
    save_to_history=None,
    enable_graph: bool | None = None,
    knowledge_graph=None,
    require_graph=None,
    local_kg_watcher=None,
    load_mcp_installs=None,
    recommend_mcps=None,
    install_mcp=None,
    mcp_public_item=None,
    hooks=None,
    allowed_workspaces_for=None,
    workspace_service=None,
) -> APIRouter:
    if tool_context is not None:
        config = tool_context.config
        ingestion_pipeline = tool_context.ingestion_pipeline
        data_dir = tool_context.data_dir
        static_dir = tool_context.static_dir
        model_router = tool_context.model_router
        require_user = tool_context.require_user
        require_admin = tool_context.require_admin
        get_current_user = tool_context.get_current_user
        clear_history = tool_context.clear_history
        append_audit_event = tool_context.append_audit_event
        enforce_rate_limit = tool_context.enforce_rate_limit
        bytes_match_extension = tool_context.bytes_match_extension
        classify_sensitive_message = tool_context.classify_sensitive_message
        save_to_history = tool_context.save_to_history
        enable_graph = tool_context.enable_graph
        knowledge_graph = tool_context.knowledge_graph
        require_graph = tool_context.require_graph
        local_kg_watcher = tool_context.local_kg_watcher
        load_mcp_installs = tool_context.load_mcp_installs
        recommend_mcps = tool_context.recommend_mcps
        install_mcp = tool_context.install_mcp
        mcp_public_item = tool_context.mcp_public_item
        hooks = tool_context.hooks
        allowed_workspaces_for = tool_context.allowed_workspaces_for
        workspace_service = tool_context.workspace_service

    api_router = APIRouter()
    HOOKS = hooks
    CONFIG = config
    DATA_DIR = data_dir
    STATIC_DIR = static_dir
    router = model_router
    ENABLE_GRAPH = enable_graph
    KNOWLEDGE_GRAPH = knowledge_graph
    LOCAL_KG_WATCHER = local_kg_watcher
    _require_graph = require_graph
    _bytes_match_extension = bytes_match_extension
    permissions_router, permission_gateway = create_permissions_router(
        config=CONFIG,
        data_dir=DATA_DIR,
        require_user=require_user,
        require_admin=require_admin,
        get_current_user=get_current_user,
    )

    # ── Direct Tool API ───────────────────────────────────────────────────────────
    
    def _policy_args(fn, *args, **kwargs) -> Dict:
        try:
            bound = inspect.signature(fn).bind_partial(*args, **kwargs)
            return dict(bound.arguments)
        except Exception:
            return dict(kwargs)

    def _tool_response(
        fn,
        *args,
        current_user: Optional[str] = None,
        source: str = "http",
        trusted_admin: bool = False,
        **kwargs,
    ):
        # Shared tool lifecycle (same path as the agent + workflow tool calls):
        # pre_tool (may block) → execute → post_tool. Keyword args are forwarded
        # to the tool and surfaced in the hook payload so read_file / edit_file /
        # grep (which need kwargs) run through the SAME lifecycle as every other
        # tool instead of bypassing it.
        tool_name = getattr(fn, "__name__", "tool")
        try:
            policy_args = _policy_args(fn, *args, **kwargs)
            if current_user is not None:
                enforce_tool_policy(
                    tool_name,
                    policy_args,
                    current_user=current_user,
                    source=source,
                    trusted_admin=trusted_admin,
                )
            result = dispatch_tool(HOOKS, tool_name, dict(kwargs), lambda: fn(*args, **kwargs), source=source)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"status": "ok", "workspace": str(AGENT_ROOT), "result": result}

    def _dispatch(tool_name, args, fn):
        # Lifecycle wrapper for callables that aren't a plain tools.* function
        # (e.g. the server's clear_history). Same pre_tool/post_tool path.
        try:
            return dispatch_tool(HOOKS, tool_name, dict(args or {}), fn, source="http")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except ToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    def _history_scope(user_email: str) -> Dict[str, Any]:
        require_auth = bool(getattr(CONFIG, "require_auth", False))
        scope: Dict[str, Any] = {
            "user_email": user_email if require_auth else None,
            "allowed_workspaces": None,
            "include_legacy_global": not require_auth,
        }
        if require_auth and user_email and allowed_workspaces_for is not None:
            scope["allowed_workspaces"] = allowed_workspaces_for(user_email)
        return scope

    def _requested_workspace(request: Request) -> Optional[str]:
        return (
            request.headers.get("X-Workspace-Id")
            or request.query_params.get("workspace_id")
            or None
        )

    def _knowledge_scope(request: Request, current_user: str, *, write: bool) -> Dict[str, str]:
        # Preserve the historical shared vault only for explicit single-user,
        # no-auth local mode. Authenticated deployments always partition by
        # both authorized workspace and account.
        if not bool(getattr(CONFIG, "require_auth", False)):
            return {}
        requested = _requested_workspace(request)
        try:
            if workspace_service is not None:
                resolver = (
                    workspace_service.resolve_write_scope
                    if write
                    else workspace_service.resolve_read_scope
                )
                workspace_id = resolver(requested, current_user)
            else:
                workspace_id = requested or "personal"
                allowed = allowed_workspaces_for(current_user) if allowed_workspaces_for else None
                if allowed is not None and workspace_id not in set(allowed):
                    raise PermissionError(f"workspace '{workspace_id}' is not readable")
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"workspace_id": str(workspace_id), "user_email": current_user}
    
    
    @api_router.post("/tools/list_dir")
    async def tools_list_dir(req: ToolPathRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(list_dir, req.path, current_user=current_user)
    
    
    @api_router.post("/tools/workspace_tree")
    async def tools_workspace_tree(req: ToolWorkspaceTreeRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(workspace_tree, req.path, req.max_depth, current_user=current_user)
    
    
    @api_router.post("/tools/read_file")
    async def tools_read_file(req: ToolReadFileRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(read_file, req.path, offset=req.offset, limit=req.limit, line_numbers=req.line_numbers, current_user=current_user)
    
    
    @api_router.post("/tools/write_file")
    async def tools_write_file(req: ToolWriteFileRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(write_file, req.path, req.content, current_user=current_user)
    
    
    @api_router.post("/tools/edit_file")
    async def tools_edit_file(req: ToolEditFileRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(edit_file, req.path, req.old_string, req.new_string, replace_all=req.replace_all, current_user=current_user)
    
    
    @api_router.post("/tools/search_files")
    async def tools_search_files(req: ToolSearchFilesRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(search_files, req.query, req.path, req.max_results, current_user=current_user)
    
    
    @api_router.post("/tools/grep")
    async def tools_grep(req: ToolGrepRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(
            grep,
            req.pattern,
            path=req.path,
            glob=req.glob,
            max_results=req.max_results,
            case_insensitive=req.case_insensitive,
            context_lines=req.context_lines,
            current_user=current_user,
        )
    
    
    @api_router.post("/tools/todo_read")
    async def tools_todo_read(request: Request):
        current_user = require_user(request)
        return _tool_response(todo_read, current_user=current_user)
    
    
    @api_router.post("/tools/todo_write")
    async def tools_todo_write(req: ToolTodoWriteRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(todo_write, req.todos, current_user=current_user)
    
    
    @api_router.post("/tools/clear_history")
    async def tools_clear_history(req: ToolClearHistoryRequest, request: Request):
        current_user = require_user(request)
        scope = _history_scope(current_user)
        result = _dispatch(
            "clear_history",
            {"keep_last": req.keep_last, **scope},
            lambda: clear_history(req.keep_last, **scope),
        )
        append_audit_event(
            "history_delete",
            user_email=current_user,
            source="tools",
            keep_last=req.keep_last,
            removed=result.get("removed", 0),
            kept=result.get("kept", 0),
        )
        return result
    
    
    @api_router.post("/tools/inspect_html")
    async def tools_inspect_html(req: ToolPathRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(inspect_html, req.path, current_user=current_user)
    
    
    @api_router.post("/tools/preview_url")
    async def tools_preview_url(req: ToolPathRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(preview_url, req.path, current_user=current_user)
    
    
    @api_router.post("/tools/create_docx")
    async def tools_create_docx(req: ToolDocxRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(create_docx, req.title, req.body, req.filename, current_user=current_user)
    
    
    @api_router.post("/tools/create_xlsx")
    async def tools_create_xlsx(req: ToolXlsxRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(create_xlsx, req.rows, req.filename, req.sheet_name, current_user=current_user)
    
    
    @api_router.post("/tools/create_pptx")
    async def tools_create_pptx(req: ToolPptxRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(create_pptx, req.title, req.slides, req.filename, current_user=current_user)
    
    
    @api_router.post("/tools/create_pdf")
    async def tools_create_pdf(req: ToolPdfRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(create_pdf, req.title, req.body, req.filename, current_user=current_user)
    
    
    @api_router.post("/tools/read_document")
    async def tools_read_document(req: ToolPathRequest, request: Request):
        current_user = require_user(request)
        raw_path = Path(req.path).expanduser()
        target = raw_path.resolve() if raw_path.is_absolute() else (AGENT_ROOT / raw_path).resolve()
        inside_agent_workspace = target == AGENT_ROOT or AGENT_ROOT in target.parents
        if not inside_agent_workspace:
            permission_gateway.require_local_approval(
                token=req.approval_token,
                path=str(target),
                action="read",
                user_email=current_user,
            )
        return _tool_response(
            read_document,
            str(target),
            current_user=current_user,
            source="workspace" if inside_agent_workspace else "approved_local",
            trusted_admin=True,
        )
    
    
    @api_router.get("/tools/pdf_pages")
    async def tools_pdf_pages(path: str, request: Request, approval_token: Optional[str] = None):
        """Render PDF pages as base64 PNG images using pypdfium2 (Apache-2.0)."""
        current_user = require_user(request)
        permission_gateway.require_local_approval(
            token=approval_token,
            path=path,
            action="read",
            user_email=current_user,
        )
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        import pypdfium2 as pdfium
        doc = None
        try:
            doc = pdfium.PdfDocument(str(target))
            total = len(doc)
            pages = []
            for i in range(min(total, 20)):  # 최대 20페이지
                page = doc[i]
                bitmap = page.render(scale=1.5)
                pil_image = bitmap.to_pil()
                buf = io.BytesIO()
                pil_image.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                pages.append({"page": i + 1, "b64": b64})
            return {"total": total, "pages": pages}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"PDF 렌더링 실패: {e}")
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception as e:
                    logging.warning("pypdfium2 doc close failed: %s", e)
    
    
    @api_router.get("/tools/download")
    async def tools_download(path: str, request: Request):
        """Serve a generated file from agent workspace for download."""
        require_user(request)
        from urllib.parse import unquote
        rel = unquote(path).lstrip("/")
        target = (AGENT_ROOT / rel).resolve()
        if AGENT_ROOT not in target.parents and target != AGENT_ROOT:
            raise HTTPException(status_code=403, detail="경로가 작업 공간 밖입니다.")
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="파일이 없습니다.")
        return FileResponse(
            path=target,
            filename=target.name,
            media_type="application/octet-stream",
        )
    
    
    @api_router.post("/upload/document")
    async def upload_document(request: Request, file: UploadFile = File(...)):
        current_user = require_user(request)
        return await process_uploaded_document(
            request=request,
            file=file,
            current_user=current_user,
            enable_graph=ENABLE_GRAPH,
            knowledge_graph=KNOWLEDGE_GRAPH,
            ingestion_pipeline=ingestion_pipeline,
            bytes_match_extension=_bytes_match_extension,
            classify_sensitive_message=classify_sensitive_message,
            append_audit_event=append_audit_event,
            enforce_rate_limit=enforce_rate_limit,
            hooks=HOOKS,
            workspace_service=workspace_service,
        )
    
    
    api_router.include_router(permissions_router)
    api_router.include_router(create_local_files_router(
        require_user=require_user,
        require_admin=require_admin,
        tool_response=_tool_response,
        permission_gateway=permission_gateway,
        knowledge_graph=KNOWLEDGE_GRAPH,
        require_graph=_require_graph,
        static_dir=STATIC_DIR,
        local_kg_watcher=LOCAL_KG_WATCHER,
        ingestion_pipeline=ingestion_pipeline,
        hooks=HOOKS,
        data_dir=DATA_DIR,
        allowed_workspaces_for=allowed_workspaces_for,
        workspace_service=workspace_service,
    ))
    api_router.include_router(create_computer_use_router(
        model_router=router,
        require_user=require_user,
        tool_response=_tool_response,
        save_to_history=save_to_history,
        hooks=HOOKS,
        append_audit_event=append_audit_event,
        workspace_service=workspace_service,
    ))

    @api_router.post("/tools/knowledge_save")
    async def tools_knowledge_save(req: ToolKnowledgeSaveRequest, request: Request):
        current_user = require_user(request)
        scope = _knowledge_scope(request, current_user, write=True)
        return _tool_response(
            knowledge_save,
            req.content,
            req.folder,
            req.title,
            current_user=current_user,
            **scope,
        )
    
    
    @api_router.post("/tools/knowledge_search")
    async def tools_knowledge_search(req: ToolKnowledgeSearchRequest, request: Request):
        current_user = require_user(request)
        scope = _knowledge_scope(request, current_user, write=False)
        return _tool_response(
            knowledge_search,
            req.query,
            req.max_results,
            current_user=current_user,
            **scope,
        )
    
    
    @api_router.get("/tools/knowledge_tree")
    async def tools_knowledge_tree(request: Request):
        current_user = require_user(request)
        scope = _knowledge_scope(request, current_user, write=False)
        return _tool_response(knowledge_tree, current_user=current_user, **scope)
    
    
    @api_router.post("/tools/obsidian_save")
    async def tools_obsidian_save(req: ToolKnowledgeSaveRequest, request: Request):
        current_user = require_user(request)
        scope = _knowledge_scope(request, current_user, write=True)
        return _tool_response(
            obsidian_save,
            req.content,
            req.folder,
            req.title,
            current_user=current_user,
            **scope,
        )
    
    
    @api_router.post("/tools/obsidian_search")
    async def tools_obsidian_search(req: ToolKnowledgeSearchRequest, request: Request):
        current_user = require_user(request)
        scope = _knowledge_scope(request, current_user, write=False)
        return _tool_response(
            obsidian_search,
            req.query,
            req.max_results,
            current_user=current_user,
            **scope,
        )
    
    
    @api_router.get("/tools/obsidian_tree")
    async def tools_obsidian_tree(request: Request):
        current_user = require_user(request)
        scope = _knowledge_scope(request, current_user, write=False)
        return _tool_response(obsidian_tree, current_user=current_user, **scope)
    
    
    @api_router.get("/obsidian/status")
    async def obsidian_status(request: Request):
        current_user = require_user(request)
        scope = _knowledge_scope(request, current_user, write=False)
        root = knowledge_scope_root(**scope)
        return {
            "status": "ok",
            "vault_root": str(root),
            "folders": [path.name for path in root.iterdir() if path.is_dir()] if root.exists() else [],
            "ocr_engine": shutil.which("tesseract") or None,
        }
    
    
    @api_router.get("/tools/git_status")
    async def tools_git_status(request: Request):
        current_user = require_user(request)
        return _tool_response(git_status, current_user=current_user)
    
    
    @api_router.post("/tools/git_diff")
    async def tools_git_diff(req: ToolGitDiffRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(git_diff, req.path, req.cwd, current_user=current_user)
    
    
    @api_router.post("/tools/git_log")
    async def tools_git_log(req: ToolGitLogRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(git_log, req.max_count, req.cwd, current_user=current_user)
    
    
    @api_router.post("/tools/git_show")
    async def tools_git_show(req: ToolGitShowRequest, request: Request):
        current_user = require_user(request)
        return _tool_response(git_show, req.revision, req.cwd, current_user=current_user)
    
    
    @api_router.post("/tools/run_command")
    async def tools_run_command(req: ToolRunCommandRequest, request: Request):
        current_user, _users = require_admin(request)
        return _tool_response(run_command, req.command, req.cwd, current_user=current_user, trusted_admin=True)
    
    
    @api_router.get("/tools/network_status")
    async def tools_network_status(request: Request):
        current_user = require_user(request)
        return _tool_response(network_status, current_user=current_user)
    
    
    @api_router.post("/tools/build_project")
    async def tools_build_project(req: ToolScriptRequest, request: Request):
        current_user, _users = require_admin(request)
        return _tool_response(build_project, req.cwd, req.script, current_user=current_user, trusted_admin=True)
    
    
    @api_router.post("/tools/deploy_project")
    async def tools_deploy_project(req: ToolScriptRequest, request: Request):
        current_user, _users = require_admin(request)
        return _tool_response(deploy_project, req.cwd, req.script, current_user=current_user, trusted_admin=True)
    
    
    @api_router.get("/tools/permissions")
    async def tools_permissions(request: Request):
        """Compact tool permission view (tool / risk / requires_approval / network).
    
        A simpler authorization-layer summary derived from TOOL_GOVERNANCE.
        Use /mcp/tools for the full 7-dimensional governance object.
        """
        require_user(request)
        return {"status": "ok", "permissions": list_tool_permissions()}

    @api_router.get("/tools/registry")
    async def tools_registry(request: Request):
        """Full ToolRegistry contract: handlers, governance, catalog, diagnostics."""
        require_user(request)
        return tool_registry_manifest()

    @api_router.get("/tools/registry/diagnostics")
    async def tools_registry_diagnostics(request: Request):
        """Small drift check for CI/admin runtime readiness views."""
        require_user(request)
        return {"status": "ok", "diagnostics": tool_registry_diagnostics()}
    
    
    # ── MCP / skills / plugins router (latticeai.api.mcp, v1.3.0) ────────────────
    api_router.include_router(create_mcp_router(
        require_user=require_user,
        require_admin=require_admin,
        append_audit_event=append_audit_event,
        load_mcp_installs=load_mcp_installs,
        recommend_mcps=recommend_mcps,
        install_mcp=install_mcp,
        mcp_public_item=mcp_public_item,
        get_tool_permission=get_tool_permission,
        tool_governance=TOOL_GOVERNANCE,
        tool_governance_default=_TOOL_GOVERNANCE_DEFAULT,
        check_tool_role=_check_tool_role,
        tool_response=_tool_response,
        require_graph=_require_graph,
        knowledge_graph=KNOWLEDGE_GRAPH,
        ingestion_pipeline=ingestion_pipeline,
        data_dir=DATA_DIR,
        allowed_workspaces_for=allowed_workspaces_for,
        workspace_service=workspace_service,
    ))

    return api_router
