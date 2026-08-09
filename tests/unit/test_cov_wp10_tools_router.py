"""Coverage for the direct-tool router (latticeai/api/tools.py).

The router is assembled through ``create_tools_router`` with injected fakes
(idiom: tests/unit/test_auth_router.py) and driven with a TestClient.  Every
``latticeai.tools`` entry point is replaced by a signature-preserving stub, so
the real policy gate still sees the real argument names while nothing touches
the developer's workspace: ``AGENT_ROOT`` is redirected into ``tmp_path`` for
all three modules that bind it.
"""

from __future__ import annotations

import base64
import inspect
import io
import sys
import time
import types
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import latticeai.api.tools as api_tools
from lattice_brain.runtime.hooks import HooksRegistry
from latticeai.api.tools import create_tools_router
from latticeai.services.router_context import ToolRouterContext
from latticeai.services.tool_dispatch import (
    DEFAULT_TOOL_DISPATCH_SERVICE,
    configure_tool_dispatch,
)
from latticeai.tools import ToolError

ADMIN = "admin@example.com"
APPROVAL_TOKEN = "wp10-approval-token"  # noqa: S105 — test fixture, not a credential

TOOL_NAMES = (
    "list_dir", "workspace_tree", "read_file", "write_file", "edit_file",
    "search_files", "grep", "todo_read", "todo_write", "inspect_html",
    "preview_url", "create_docx", "create_xlsx", "create_pptx", "create_pdf",
    "read_document", "knowledge_save", "knowledge_search", "knowledge_tree",
    "obsidian_save", "obsidian_search", "obsidian_tree", "git_status",
    "git_diff", "git_log", "git_show", "run_command", "network_status",
    "build_project", "deploy_project",
)


def _stub_for(real, calls):
    """A recorder with the real tool's name and signature.

    Keeping ``__signature__`` means ``_policy_args`` still binds the real
    parameter names, so the governance gate is exercised with the same
    argument dict production would hand it.
    """
    name = real.__name__

    def _call(*args, **kwargs):
        calls.append((name, list(args), dict(kwargs)))
        return {"tool": name, "args": list(args), "kwargs": dict(kwargs)}

    _call.__name__ = name
    _call.__signature__ = inspect.signature(real)
    return _call


class _Harness:
    def __init__(
        self,
        monkeypatch,
        tmp_path,
        *,
        require_auth=False,
        workspace_service=None,
        allowed_workspaces_for=None,
        user=ADMIN,
        role="admin",
        clear_history=None,
        enable_graph=False,
    ):
        self.calls = []
        self.audit = []
        self.saved = []
        self.workspace = tmp_path / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        static_dir = tmp_path / "static"
        static_dir.mkdir(parents=True, exist_ok=True)

        # Every module that binds AGENT_ROOT gets the sandbox, so path
        # confinement, governance existence probes and zip all agree.
        for module in ("latticeai.api.tools", "latticeai.tools", "latticeai.services.tool_dispatch"):
            monkeypatch.setattr(module + ".AGENT_ROOT", self.workspace)

        configure_tool_dispatch(
            load_users=lambda: {},
            get_user_role=lambda _email, _users=None: role,
        )
        for name in TOOL_NAMES:
            monkeypatch.setattr(
                api_tools, name, _stub_for(getattr(api_tools, name), self.calls),
            )

        gateways = []
        real_permissions = api_tools.create_permissions_router

        def _capture(**kwargs):
            router, gateway = real_permissions(**kwargs)
            gateways.append(gateway)
            return router, gateway

        monkeypatch.setattr(api_tools, "create_permissions_router", _capture)

        self.hooks = HooksRegistry(data_dir / "hooks.json")
        app = FastAPI()
        app.include_router(create_tools_router(
            config=SimpleNamespace(
                require_auth=require_auth,
                discord_permission_webhook="",
                discord_bot_token="",
                discord_permission_channel="",
                permission_monitor_secret="",
                port=4825,
            ),
            ingestion_pipeline=None,
            data_dir=data_dir,
            static_dir=static_dir,
            model_router=SimpleNamespace(current_model_id=None),
            require_user=lambda _request: user,
            require_admin=lambda _request: (user, {}),
            get_current_user=lambda _request: user,
            clear_history=clear_history or (lambda keep_last, **scope: {"removed": 0, "kept": 0}),
            append_audit_event=lambda event, **payload: self.audit.append((event, payload)),
            enforce_rate_limit=lambda *a, **k: None,
            bytes_match_extension=lambda *a, **k: True,
            classify_sensitive_message=lambda *a, **k: None,
            save_to_history=lambda *a, **k: self.saved.append((a, k)),
            enable_graph=enable_graph,
            knowledge_graph=None,
            require_graph=lambda: (_ for _ in ()).throw(HTTPException(503, "graph disabled")),
            local_kg_watcher=None,
            load_mcp_installs=lambda: {"installed": {}},
            recommend_mcps=lambda *a, **k: [],
            install_mcp=lambda *a, **k: {"ok": True},
            mcp_public_item=lambda item, _installs: dict(item),
            hooks=self.hooks,
            allowed_workspaces_for=allowed_workspaces_for,
            workspace_service=workspace_service,
        ))
        self.gateway = gateways[0]
        self.client = TestClient(app, raise_server_exceptions=False)

    def approve(self, path, action="read", user=ADMIN, token=APPROVAL_TOKEN):
        """Seed a granted local-file approval the real gateway will accept."""
        normalized = self.gateway.normalize_local_path_for_approval(str(path))
        self.gateway.local_approvals[self.gateway.token_hash(token)] = {
            "approved": True,
            "user_email": user,
            "path": normalized,
            "action": action,
            "expires_at": time.time() + 3600,
        }
        return token

    def by_tool(self, name):
        return [entry for entry in self.calls if entry[0] == name]


@pytest.fixture()
def restore_dispatch(monkeypatch):
    """Let a test call configure_tool_dispatch() without leaking the config."""
    service = DEFAULT_TOOL_DISPATCH_SERVICE
    for field in ("load_users", "get_user_role", "permission_mode"):
        monkeypatch.setattr(service, field, getattr(service, field))
    return service


@pytest.fixture()
def harness(monkeypatch, tmp_path, restore_dispatch):
    def _make(**kwargs):
        return _Harness(monkeypatch, tmp_path, **kwargs)

    return _make


# ── construction contract ────────────────────────────────────────────────────
def test_router_refuses_to_build_without_the_required_directories(tmp_path):
    with pytest.raises(RuntimeError, match="data_dir and static_dir"):
        create_tools_router(data_dir=None, static_dir=tmp_path)

    with pytest.raises(RuntimeError, match="data_dir and static_dir"):
        create_tools_router(data_dir=tmp_path, static_dir=None)


def test_router_can_be_assembled_from_a_typed_context(monkeypatch, tmp_path, restore_dispatch):
    """The ToolRouterContext form must wire the same dependencies as kwargs."""
    calls = []
    monkeypatch.setattr(api_tools, "list_dir", _stub_for(api_tools.list_dir, calls))
    configure_tool_dispatch(
        load_users=lambda: {},
        get_user_role=lambda _email, _users=None: "admin",
    )
    context = ToolRouterContext(
        config=SimpleNamespace(
            require_auth=False,
            discord_permission_webhook="",
            discord_bot_token="",
            discord_permission_channel="",
            permission_monitor_secret="",
            port=4825,
        ),
        ingestion_pipeline=None,
        data_dir=tmp_path / "ctx-data",
        static_dir=tmp_path / "ctx-static",
        model_router=SimpleNamespace(current_model_id=None),
        require_user=lambda _request: ADMIN,
        require_admin=lambda _request: (ADMIN, {}),
        get_current_user=lambda _request: ADMIN,
        clear_history=lambda keep_last, **scope: {"removed": 0, "kept": 0},
        append_audit_event=lambda event, **payload: None,
        enforce_rate_limit=lambda *a, **k: None,
        bytes_match_extension=lambda *a, **k: True,
        classify_sensitive_message=lambda *a, **k: None,
        save_to_history=lambda *a, **k: None,
        enable_graph=False,
        knowledge_graph=None,
        require_graph=lambda: None,
        local_kg_watcher=None,
        load_mcp_installs=lambda: {"installed": {}},
        recommend_mcps=lambda *a, **k: [],
        install_mcp=lambda *a, **k: {"ok": True},
        mcp_public_item=lambda item, _installs: dict(item),
        hooks=None,
        workspace_service=None,
        allowed_workspaces_for=None,
    )
    (tmp_path / "ctx-data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ctx-static").mkdir(parents=True, exist_ok=True)

    app = FastAPI()
    app.include_router(create_tools_router(tool_context=context))
    response = TestClient(app, raise_server_exceptions=False).post(
        "/tools/list_dir", json={"path": "src"},
    )

    assert response.status_code == 200, response.text
    assert calls == [("list_dir", ["src"], {})]


# ── the direct tool table ────────────────────────────────────────────────────
TOOL_ROUTES = [
    ("post", "/tools/list_dir", {"path": "src"}, "list_dir", ["src"], {}),
    ("post", "/tools/workspace_tree", {"path": "src", "max_depth": 2},
     "workspace_tree", ["src", 2], {}),
    ("post", "/tools/read_file", {"path": "a.md", "offset": 2, "limit": 5, "line_numbers": False},
     "read_file", ["a.md"], {"offset": 2, "limit": 5, "line_numbers": False}),
    ("post", "/tools/write_file", {"path": "wp10-scratch/new.txt", "content": "hi"},
     "write_file", ["wp10-scratch/new.txt", "hi"], {}),
    ("post", "/tools/edit_file",
     {"path": "wp10-scratch/new.txt", "old_string": "a", "new_string": "b", "replace_all": True},
     "edit_file", ["wp10-scratch/new.txt", "a", "b"], {"replace_all": True}),
    ("post", "/tools/search_files", {"query": "todo", "path": "src", "max_results": 3},
     "search_files", ["todo", "src", 3], {}),
    ("post", "/tools/grep",
     {"pattern": "TODO", "path": "src", "glob": "*.py", "max_results": 7,
      "case_insensitive": True, "context_lines": 2},
     "grep", ["TODO"],
     {"path": "src", "glob": "*.py", "max_results": 7,
      "case_insensitive": True, "context_lines": 2}),
    ("post", "/tools/todo_read", None, "todo_read", [], {}),
    ("post", "/tools/todo_write", {"todos": [{"id": 1}]}, "todo_write", [[{"id": 1}]], {}),
    ("post", "/tools/inspect_html", {"path": "page.html"}, "inspect_html", ["page.html"], {}),
    ("post", "/tools/preview_url", {"path": "page.html"}, "preview_url", ["page.html"], {}),
    ("post", "/tools/create_docx", {"title": "T", "body": "B", "filename": "wp10.docx"},
     "create_docx", ["T", "B", "wp10.docx"], {}),
    ("post", "/tools/create_xlsx", {"rows": [[1, 2]], "filename": "wp10.xlsx", "sheet_name": "S"},
     "create_xlsx", [[[1, 2]], "wp10.xlsx", "S"], {}),
    ("post", "/tools/create_pptx", {"title": "T", "slides": [{"title": "s"}], "filename": "wp10.pptx"},
     "create_pptx", ["T", [{"title": "s"}], "wp10.pptx"], {}),
    ("post", "/tools/create_pdf", {"title": "T", "body": "B", "filename": "wp10.pdf"},
     "create_pdf", ["T", "B", "wp10.pdf"], {}),
    ("get", "/tools/git_status", None, "git_status", [], {}),
    ("post", "/tools/git_diff", {"path": "a.py", "cwd": "."}, "git_diff", ["a.py", "."], {}),
    ("post", "/tools/git_log", {"max_count": 3, "cwd": "."}, "git_log", [3, "."], {}),
    ("post", "/tools/git_show", {"revision": "HEAD~1", "cwd": "."},
     "git_show", ["HEAD~1", "."], {}),
    ("post", "/tools/run_command", {"command": "echo hi", "cwd": "."},
     "run_command", ["echo hi", "."], {}),
    ("get", "/tools/network_status", None, "network_status", [], {}),
    ("post", "/tools/build_project", {"cwd": ".", "script": "build"},
     "build_project", [".", "build"], {}),
    ("post", "/tools/deploy_project", {"cwd": ".", "script": "deploy"},
     "deploy_project", [".", "deploy"], {}),
]


@pytest.mark.parametrize(
    ("method", "path", "payload", "tool", "args", "kwargs"),
    TOOL_ROUTES,
    ids=[route[1].rsplit("/", 1)[-1] for route in TOOL_ROUTES],
)
def test_tool_routes_forward_their_request_to_the_named_tool(
    harness, method, path, payload, tool, args, kwargs,
):
    app = harness()

    response = (
        app.client.get(path) if method == "get"
        else app.client.post(path, json=payload)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["workspace"] == str(app.workspace)
    assert body["result"]["tool"] == tool
    assert app.by_tool(tool) == [(tool, args, kwargs)]


def test_knowledge_routes_run_unscoped_when_auth_is_disabled(harness):
    app = harness(require_auth=False)

    saved = app.client.post("/tools/knowledge_save", json={"content": "note", "folder": "00_Raw"})
    searched = app.client.post("/tools/knowledge_search", json={"query": "note", "max_results": 2})
    tree = app.client.get("/tools/knowledge_tree")
    obs_saved = app.client.post("/tools/obsidian_save", json={"content": "note"})
    obs_searched = app.client.post("/tools/obsidian_search", json={"query": "note"})
    obs_tree = app.client.get("/tools/obsidian_tree")

    for response in (saved, searched, tree, obs_saved, obs_searched, obs_tree):
        assert response.status_code == 200, response.text
    assert app.by_tool("knowledge_save") == [("knowledge_save", ["note", "00_Raw", None], {})]
    assert app.by_tool("knowledge_search") == [("knowledge_search", ["note", 2], {})]
    assert app.by_tool("knowledge_tree") == [("knowledge_tree", [], {})]
    assert app.by_tool("obsidian_save") == [("obsidian_save", ["note", "00_Raw", None], {})]
    assert app.by_tool("obsidian_search") == [("obsidian_search", ["note", 5], {})]
    assert app.by_tool("obsidian_tree") == [("obsidian_tree", [], {})]


class _WorkspaceService:
    def __init__(self):
        self.reads = []
        self.writes = []

    def resolve_read_scope(self, requested, user):
        self.reads.append((requested, user))
        return "org:read"

    def resolve_write_scope(self, requested, user):
        self.writes.append((requested, user))
        return "org:write"


def test_knowledge_routes_partition_by_resolved_workspace(harness):
    service = _WorkspaceService()
    app = harness(require_auth=True, workspace_service=service)

    saved = app.client.post(
        "/tools/knowledge_save",
        headers={"X-Workspace-Id": "org:requested"},
        json={"content": "note", "title": "T"},
    )
    searched = app.client.post(
        "/tools/obsidian_search?workspace_id=org:query", json={"query": "note"},
    )

    assert saved.status_code == 200, saved.text
    assert searched.status_code == 200, searched.text
    assert service.writes == [("org:requested", ADMIN)]
    assert service.reads == [("org:query", ADMIN)]
    assert app.by_tool("knowledge_save")[0][2] == {
        "workspace_id": "org:write", "user_email": ADMIN,
    }
    assert app.by_tool("obsidian_search")[0][2] == {
        "workspace_id": "org:read", "user_email": ADMIN,
    }


def test_knowledge_scope_falls_back_to_the_allowed_workspace_list(harness):
    app = harness(
        require_auth=True,
        allowed_workspaces_for=lambda _email: ["personal", "org:team"],
    )

    allowed = app.client.get("/tools/knowledge_tree", headers={"X-Workspace-Id": "org:team"})
    default = app.client.get("/tools/obsidian_tree")

    assert allowed.status_code == 200, allowed.text
    assert app.by_tool("knowledge_tree")[0][2] == {
        "workspace_id": "org:team", "user_email": ADMIN,
    }
    assert default.status_code == 200
    assert app.by_tool("obsidian_tree")[0][2]["workspace_id"] == "personal"


def test_knowledge_scope_refuses_an_unlisted_workspace(harness):
    app = harness(require_auth=True, allowed_workspaces_for=lambda _email: ["personal"])

    response = app.client.get("/tools/knowledge_tree", headers={"X-Workspace-Id": "org:other"})

    assert response.status_code == 403
    assert "org:other" in response.json()["detail"]
    assert app.by_tool("knowledge_tree") == []


def test_obsidian_status_reports_the_scoped_vault_root(harness):
    # A workspace id unique to this test, so the partition directory is
    # guaranteed not to exist no matter what else the suite has written.
    app = harness(require_auth=True, allowed_workspaces_for=lambda _email: ["wp10:vault"])

    response = app.client.get("/obsidian/status", headers={"X-Workspace-Id": "wp10:vault"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert ".lattice-scopes" in body["vault_root"]  # partitioned, not the shared vault
    assert body["folders"] == []
    assert "ocr_engine" in body


# ── lifecycle + policy edges of _tool_response / _dispatch ───────────────────
def test_tool_error_becomes_a_400(harness, monkeypatch):
    app = harness()

    def _list_dir(path="."):
        raise ToolError("Directory does not exist.")

    monkeypatch.setattr(api_tools, "list_dir", _list_dir)

    response = app.client.post("/tools/list_dir", json={"path": "nope"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Directory does not exist."


def test_a_blocking_pre_tool_hook_becomes_a_403(harness):
    app = harness()
    app.hooks.register_hook(
        "builtin:tool-permission-gate",
        lambda ctx: ctx.block("read_file denied by policy hook"),
    )

    response = app.client.post("/tools/read_file", json={"path": "a.md"})

    assert response.status_code == 403
    assert "denied by policy hook" in response.json()["detail"]
    assert app.by_tool("read_file") == []


def test_policy_args_fall_back_to_kwargs_when_the_signature_is_opaque(harness, monkeypatch):
    app = harness()
    seen = []

    def _opaque(*args, **kwargs):
        seen.append((args, kwargs))
        return {"listed": list(args)}

    _opaque.__name__ = "list_dir"
    # A callable whose signature cannot be introspected (C extension, exotic
    # decorator): _policy_args must degrade to the kwargs instead of raising.
    _opaque.__signature__ = object()
    monkeypatch.setattr(api_tools, "list_dir", _opaque)

    response = app.client.post("/tools/list_dir", json={"path": "src"})

    assert response.status_code == 200, response.text
    assert response.json()["result"] == {"listed": ["src"]}
    assert seen == [(("src",), {})]


def test_admin_only_tool_is_refused_for_a_plain_user(harness):
    app = harness(role="user", user="viewer@example.com")

    response = app.client.get("/tools/network_status")

    assert response.status_code == 403
    assert "관리자 전용" in response.json()["detail"]
    assert app.by_tool("network_status") == []


def test_circuit_breaker_blocks_a_destructive_shell_command(harness):
    app = harness()

    response = app.client.post("/tools/run_command", json={"command": "rm -rf /"})

    assert response.status_code == 403
    assert "circuit breaker" in response.json()["detail"]
    assert app.by_tool("run_command") == []


# ── clear_history ────────────────────────────────────────────────────────────
def test_clear_history_audits_the_deletion_without_auth_scope(harness):
    seen = []
    app = harness(
        clear_history=lambda keep_last, **scope: seen.append((keep_last, scope))
        or {"removed": 4, "kept": 1},
    )

    response = app.client.post("/tools/clear_history", json={"keep_last": 1})

    assert response.status_code == 200, response.text
    assert response.json() == {"removed": 4, "kept": 1}
    assert seen == [(1, {
        "user_email": None,
        "allowed_workspaces": None,
        "include_legacy_global": True,
    })]
    event, payload = app.audit[-1]
    assert event == "history_delete"
    assert payload["removed"] == 4
    assert payload["kept"] == 1
    assert payload["keep_last"] == 1


def test_clear_history_scopes_to_the_callers_allowed_workspaces(harness):
    seen = []
    app = harness(
        require_auth=True,
        allowed_workspaces_for=lambda email: ["personal", "org:" + email],
        clear_history=lambda keep_last, **scope: seen.append((keep_last, scope))
        or {"removed": 0, "kept": 9},
    )

    response = app.client.post("/tools/clear_history", json={"keep_last": 9})

    assert response.status_code == 200, response.text
    assert seen == [(9, {
        "user_email": ADMIN,
        "allowed_workspaces": ["personal", "org:" + ADMIN],
        "include_legacy_global": False,
    })]


def test_clear_history_maps_a_tool_error_to_400(harness):
    def _fails(_keep_last, **_scope):
        raise ToolError("history store is locked")

    app = harness(clear_history=_fails)

    response = app.client.post("/tools/clear_history", json={"keep_last": 0})

    assert response.status_code == 400
    assert response.json()["detail"] == "history store is locked"
    assert app.audit == []


def test_clear_history_is_blocked_by_a_pre_tool_hook(harness):
    app = harness()
    app.hooks.register_hook(
        "builtin:tool-permission-gate", lambda ctx: ctx.block("history is protected"),
    )

    response = app.client.post("/tools/clear_history", json={"keep_last": 0})

    assert response.status_code == 403
    assert "history is protected" in response.json()["detail"]
    assert app.audit == []


# ── read_document + local approval ───────────────────────────────────────────
def test_read_document_inside_the_workspace_needs_no_approval(harness):
    app = harness()
    sources = []
    app.hooks.register_hook(
        "builtin:tool-permission-gate", lambda ctx: sources.append(ctx.payload.get("source")),
    )

    response = app.client.post("/tools/read_document", json={"path": "notes/report.pdf"})

    assert response.status_code == 200, response.text
    assert app.by_tool("read_document") == [
        ("read_document", [str(app.workspace / "notes/report.pdf")], {}),
    ]
    assert sources == ["workspace"]


def test_read_document_outside_the_workspace_requires_an_approval_token(harness, tmp_path):
    app = harness()
    outside = tmp_path / "elsewhere" / "secret.pdf"

    response = app.client.post("/tools/read_document", json={"path": str(outside)})

    assert response.status_code == 403
    assert "승인 토큰" in response.json()["detail"]
    assert app.by_tool("read_document") == []


def test_read_document_outside_the_workspace_runs_with_an_approval(harness, tmp_path):
    app = harness()
    sources = []
    app.hooks.register_hook(
        "builtin:tool-permission-gate", lambda ctx: sources.append(ctx.payload.get("source")),
    )
    outside = tmp_path / "elsewhere" / "secret.pdf"
    token = app.approve(outside)

    response = app.client.post(
        "/tools/read_document", json={"path": str(outside), "approval_token": token},
    )

    assert response.status_code == 200, response.text
    assert app.by_tool("read_document") == [("read_document", [str(outside)], {})]
    assert sources == ["approved_local"]


# ── pdf_pages ────────────────────────────────────────────────────────────────
def _fake_pdfium(monkeypatch, *, pages=2, open_error=None, close_error=None, closed=None):
    module = types.ModuleType("pypdfium2")

    class _Bitmap:
        def to_pil(self):
            return _Image()

    class _Image:
        def save(self, buffer, format):  # noqa: A002 — pypdfium2's PIL contract
            buffer.write(b"PNG:" + format.encode())

    class _Page:
        def render(self, scale):
            assert scale == 1.5
            return _Bitmap()

    class _Doc:
        def __init__(self, _path):
            if open_error is not None:
                raise open_error

        def __len__(self):
            return pages

        def __getitem__(self, _index):
            return _Page()

        def close(self):
            if closed is not None:
                closed.append(True)
            if close_error is not None:
                raise close_error

    module.PdfDocument = _Doc
    monkeypatch.setitem(sys.modules, "pypdfium2", module)
    return module


def test_pdf_pages_renders_at_most_twenty_pages(harness, monkeypatch, tmp_path):
    app = harness()
    closed = []
    _fake_pdfium(monkeypatch, pages=25, closed=closed)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    token = app.approve(pdf)

    response = app.client.get(
        "/tools/pdf_pages", params={"path": str(pdf), "approval_token": token},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 25
    assert len(body["pages"]) == 20
    assert body["pages"][0] == {"page": 1, "b64": base64.b64encode(b"PNG:PNG").decode()}
    assert closed == [True]


def test_pdf_pages_requires_an_approval_token(harness, tmp_path):
    app = harness()
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    response = app.client.get("/tools/pdf_pages", params={"path": str(pdf)})

    assert response.status_code == 403


def test_pdf_pages_returns_404_for_a_missing_file(harness, tmp_path):
    app = harness()
    missing = tmp_path / "gone.pdf"
    token = app.approve(missing)

    response = app.client.get(
        "/tools/pdf_pages", params={"path": str(missing), "approval_token": token},
    )

    assert response.status_code == 404


def test_pdf_pages_reports_a_render_failure(harness, monkeypatch, tmp_path):
    app = harness()
    _fake_pdfium(monkeypatch, open_error=RuntimeError("corrupt xref"))
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"%PDF-1.4 broken")
    token = app.approve(pdf)

    response = app.client.get(
        "/tools/pdf_pages", params={"path": str(pdf), "approval_token": token},
    )

    assert response.status_code == 500
    assert "corrupt xref" in response.json()["detail"]


def test_pdf_pages_survives_a_close_failure(harness, monkeypatch, tmp_path):
    app = harness()
    _fake_pdfium(monkeypatch, pages=1, close_error=RuntimeError("handle already gone"))
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    token = app.approve(pdf)

    response = app.client.get(
        "/tools/pdf_pages", params={"path": str(pdf), "approval_token": token},
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["pages"]) == 1


# ── download / download_zip ──────────────────────────────────────────────────
def test_download_serves_a_workspace_file(harness):
    app = harness()
    target = app.workspace / "generated docs" / "report.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello", encoding="utf-8")

    response = app.client.get("/tools/download", params={"path": "/generated docs/report.txt"})

    assert response.status_code == 200, response.text
    assert response.content == b"hello"
    assert "report.txt" in response.headers["content-disposition"]


def test_download_refuses_a_path_outside_the_workspace(harness):
    app = harness()

    response = app.client.get("/tools/download", params={"path": "../escape.txt"})

    assert response.status_code == 403


def test_download_returns_404_for_a_missing_file(harness):
    app = harness()

    response = app.client.get("/tools/download", params={"path": "missing.txt"})

    assert response.status_code == 404


def test_download_zip_bundles_a_project_directory(harness):
    app = harness()
    project = app.workspace / "todo-app"
    project.mkdir(parents=True, exist_ok=True)
    (project / "index.html").write_text("<h1>todo</h1>", encoding="utf-8")

    response = app.client.get("/tools/download_zip", params={"path": "todo-app"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert 'filename="todo-app.zip"' in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["todo-app/index.html"]


def test_download_zip_refuses_a_path_outside_the_workspace(harness):
    app = harness()

    response = app.client.get("/tools/download_zip", params={"path": "../escape"})

    assert response.status_code == 403


def test_download_zip_returns_404_for_a_missing_directory(harness):
    app = harness()

    response = app.client.get("/tools/download_zip", params={"path": "no-such-project"})

    assert response.status_code == 404


def test_download_zip_refuses_the_workspace_root(harness):
    app = harness()

    response = app.client.get("/tools/download_zip", params={"path": ""})

    assert response.status_code == 400
    assert "workspace root" in response.json()["detail"]


# ── upload ───────────────────────────────────────────────────────────────────
def test_upload_document_delegates_to_the_upload_service(harness, monkeypatch):
    app = harness(enable_graph=True)
    seen = {}

    async def _process(**kwargs):
        seen.update(kwargs)
        return {"status": "ok", "filename": kwargs["file"].filename}

    monkeypatch.setattr(api_tools, "process_uploaded_document", _process)

    response = app.client.post(
        "/upload/document",
        files={"file": ("notes.txt", b"body bytes", "text/plain")},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "filename": "notes.txt"}
    assert seen["current_user"] == ADMIN
    assert seen["enable_graph"] is True
    assert seen["hooks"] is app.hooks


# ── registry / permission views ──────────────────────────────────────────────
def test_permission_and_registry_views_expose_the_tool_registry(harness):
    app = harness()

    permissions = app.client.get("/tools/permissions")
    registry = app.client.get("/tools/registry")
    diagnostics = app.client.get("/tools/registry/diagnostics")

    assert permissions.status_code == 200
    assert permissions.json()["status"] == "ok"
    names = {item["tool"] for item in permissions.json()["permissions"]}
    assert {"list_dir", "run_command"} <= names

    assert registry.status_code == 200
    manifest = registry.json()
    assert manifest["schema_version"] == "tool-registry-contract/v1"
    assert "run_command" in {entry["name"] for entry in manifest["tools"]}

    assert diagnostics.status_code == 200
    assert diagnostics.json()["status"] == "ok"
    assert isinstance(diagnostics.json()["diagnostics"], dict)
