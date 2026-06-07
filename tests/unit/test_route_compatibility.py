"""v1.3.0 server_app decomposition safety net.

Freezes the public route surface and key startup/streaming/model/MCP/KG
contracts so the large router/service extraction cannot silently drop or rename
an endpoint. If a route moves between modules its path must stay identical;
this test asserts the full path set is preserved.
"""

import importlib
import inspect

import pytest

# Frozen baseline captured from v1.2.0 (every public path that must keep existing).
BASELINE_PATHS = [
    "/", "/account", "/account/change-password", "/account/profile", "/admin",
    "/admin/audit", "/admin/invite-link",
    "/admin/security/conversations/{conversation_id}",
    "/admin/security/conversations/{conversation_id}/raw", "/admin/security/events",
    "/admin/security/events/{event_id}", "/admin/security/export",
    "/admin/security/files", "/admin/security/files/{file_id}",
    "/admin/security/files/{file_id}/content", "/admin/security/overview",
    "/admin/security/raw", "/admin/security/users", "/admin/sensitivity",
    "/admin/sso", "/admin/stats", "/admin/summary", "/admin/users",
    "/admin/users/{email:path}", "/admin/vpc", "/agent", "/agent/eval",
    "/agent/resume", "/auth/sso/callback", "/auth/sso/config", "/auth/sso/login",
    "/chat", "/cu/agent", "/cu/click", "/cu/drag", "/cu/key", "/cu/move",
    "/cu/open_app", "/cu/open_url", "/cu/screenshot", "/cu/scroll", "/cu/status",
    "/cu/type", "/engines", "/engines/install", "/engines/prepare-model",
    "/engines/prepare-model/stream", "/engines/pull-model", "/engines/verify-cloud",
    "/garden", "/garden/tree", "/graph", "/health", "/history",
    "/history/conversations", "/history/conversations/{conversation_id:path}",
    "/history/search", "/knowledge-graph", "/knowledge-graph/context",
    "/knowledge-graph/graph", "/knowledge-graph/ingest",
    "/knowledge-graph/local/audit", "/knowledge-graph/local/index",
    "/knowledge-graph/local/roots", "/knowledge-graph/local/sources",
    "/knowledge-graph/local/tree", "/knowledge-graph/local/watch/status",
    "/knowledge-graph/local/watch/stop", "/knowledge-graph/neighbors/{node_id:path}",
    "/knowledge-graph/schema", "/knowledge-graph/search", "/knowledge-graph/stats",
    "/local/list", "/local/read", "/local/serve", "/local/sysinfo", "/local/write",
    "/login", "/logout", "/manifest.json", "/mcp/call", "/mcp/claude-code-servers",
    "/mcp/connectors/{mcp_id}", "/mcp/custom", "/mcp/custom/{mcp_id:path}",
    "/mcp/install", "/mcp/installed", "/mcp/recommend", "/mcp/registry/refresh",
    "/mcp/tools", "/mode", "/models", "/models/compat-profiles", "/models/load",
    "/models/switch/{model_id:path}", "/models/unload-all",
    "/models/unload/{model_id:path}", "/obsidian/status", "/onboarding",
    "/permissions/approve/{token}", "/permissions/deny/{token}",
    "/permissions/open/{permission_id}", "/permissions/pending",
    "/permissions/status/{token}", "/plugins/directory",
    "/plugins/directory/refresh", "/register", "/runtime_features", "/setup/auto",
    "/setup/install", "/setup/open-auth/{mcp_id}", "/setup/scan",
    "/setup/set-api-key", "/skills/install", "/skills/list", "/skills/marketplace",
    "/skills/marketplace/refresh", "/status", "/sw.js", "/tools/build_project",
    "/tools/chrome_status", "/tools/clear_history", "/tools/computer_use_status",
    "/tools/create_docx", "/tools/create_pdf", "/tools/create_pptx",
    "/tools/create_xlsx", "/tools/deploy_project", "/tools/download",
    "/tools/edit_file", "/tools/git_diff", "/tools/git_log", "/tools/git_show",
    "/tools/git_status", "/tools/grep", "/tools/inspect_html",
    "/tools/knowledge_save", "/tools/knowledge_search", "/tools/knowledge_tree",
    "/tools/list_dir", "/tools/network_status", "/tools/obsidian_save",
    "/tools/obsidian_search", "/tools/obsidian_tree", "/tools/pdf_pages",
    "/tools/permissions", "/tools/preview_url", "/tools/read_document",
    "/tools/read_file", "/tools/run_command", "/tools/search_files",
    "/tools/todo_read", "/tools/todo_write", "/tools/workspace_tree",
    "/tools/write_file", "/upload/document", "/vpc/status", "/workspace",
    "/workspace/activate", "/workspace/agents", "/workspace/agents/runs",
    "/workspace/audit-timeline", "/workspace/computer-memory",
    "/workspace/computer-memory/activity", "/workspace/editions",
    "/workspace/indexing", "/workspace/indexing/{source_id}/pause",
    "/workspace/indexing/{source_id}/remove", "/workspace/indexing/{source_id}/resume",
    "/workspace/memories", "/workspace/memories/search",
    "/workspace/memories/{memory_id}", "/workspace/onboarding/complete",
    "/workspace/onboarding/hardware", "/workspace/onboarding/model-recommendations",
    "/workspace/onboarding/status", "/workspace/onboarding/step", "/workspace/orgs",
    "/workspace/orgs/{workspace_id}", "/workspace/orgs/{workspace_id}/archive",
    "/workspace/orgs/{workspace_id}/members",
    "/workspace/orgs/{workspace_id}/members/{user_id}",
    "/workspace/orgs/{workspace_id}/summary", "/workspace/os", "/workspace/registry",
    "/workspace/relationships/{node_id:path}", "/workspace/skills",
    "/workspace/skills/disable", "/workspace/skills/enable",
    "/workspace/skills/install", "/workspace/skills/uninstall",
    "/workspace/skills/update", "/workspace/snapshots", "/workspace/snapshots/compare",
    "/workspace/snapshots/{snapshot_id}", "/workspace/snapshots/{snapshot_id}/export",
    "/workspace/snapshots/{snapshot_id}/{area}", "/workspace/time-machine",
    "/workspace/time-machine/{snapshot_id}/{area}", "/workspace/traces",
    "/workspace/vscode/send", "/workspace/workflows",
    "/workspace/workflows/{workflow_id}/events",
]


@pytest.fixture(scope="module")
def app():
    return importlib.import_module("server").app


def _paths(app):
    return {getattr(r, "path", "") for r in app.routes}


def test_all_baseline_routes_preserved(app):
    current = _paths(app)
    missing = sorted(p for p in BASELINE_PATHS if p not in current)
    assert not missing, f"routes dropped during decomposition: {missing}"


def test_route_count_does_not_collapse(app):
    # Guard against an extraction wiping a whole router include.
    assert len(app.routes) >= len(BASELINE_PATHS)


def test_import_paths_and_identity():
    server = importlib.import_module("server")
    sa = importlib.import_module("latticeai.server_app")
    assert server.app is sa.app
    assert type(server.app).__name__ == "FastAPI"


def test_app_version_is_derived(app):
    # /health version derives from WORKSPACE_OS_VERSION via APP_VERSION.
    from latticeai.core.workspace_os import WORKSPACE_OS_VERSION
    assert app.version == WORKSPACE_OS_VERSION


def test_chat_streaming_contract(app):
    # The /chat POST endpoint must remain and the chat module must use a
    # StreamingResponse (chunked streaming contract preserved).
    chat_routes = [r for r in app.routes if getattr(r, "path", "") == "/chat"
                   and "POST" in (getattr(r, "methods", set()) or set())]
    assert chat_routes, "/chat POST route missing"
    endpoint = chat_routes[0].endpoint
    module = importlib.import_module(endpoint.__module__)
    src = inspect.getsource(module)
    assert "StreamingResponse" in src, "chat module no longer references StreamingResponse"


def test_model_and_engine_routes_present(app):
    paths = _paths(app)
    for p in ("/models", "/models/load", "/models/compat-profiles", "/engines",
              "/engines/verify-cloud", "/engines/install"):
        assert p in paths, f"model/engine route missing: {p}"


def test_v3_app_route_present(app):
    assert "/app" in _paths(app), "v3 /app shell route missing"


def test_mcp_and_kg_routes_present(app):
    paths = _paths(app)
    for p in ("/mcp/tools", "/mcp/call", "/mcp/installed",
              "/knowledge-graph/search", "/knowledge-graph/stats"):
        assert p in paths, f"mcp/kg route missing: {p}"
