"""``/skills/*``, ``/plugins/*`` and ``/mcp/call`` handlers of the MCP router.

Marketplace/directory fetchers and ``SKILLS_DIR`` are redirected at ``tmp_path``,
so these exercise the routing, filtering and workspace-scoping logic only.
"""

from __future__ import annotations

from latticeai.api import mcp as mcp_api
from latticeai.core import mcp_registry
from tests.unit.test_cov_wp08_api_mcp import EN, build_mcp_client

_MARKET_SKILLS = [
    {
        "plugin": "docs-kit",
        "skill": "writing",
        "category": "Documentation",
        "author": "Anthropic",
        "description": "write docs",
    },
    {
        "plugin": "airtable",
        "skill": "bases",
        "category": "productivity",
        "author": "Airtable",
        "description": "manage bases",
    },
]

_DIRECTORY = [
    {
        "name": "docs-kit",
        "description": "documentation helper",
        "category": "core",
        "author": "Anthropic",
        "license": "Apache-2.0",
    },
    {
        "name": "airtable",
        "description": "bases and records",
        "category": "productivity",
        "author": "Airtable",
        "license": "MIT",
    },
]


def _skills_dir(monkeypatch, tmp_path, names=()):
    root = tmp_path / "skills"
    root.mkdir()
    for name in names:
        (root / name).mkdir()
    monkeypatch.setattr(mcp_api, "SKILLS_DIR", root)
    return root


def _marketplace(monkeypatch, skills):
    async def fetch():
        return skills

    monkeypatch.setattr(mcp_api, "_fetch_skills_marketplace", fetch)


def _directory(monkeypatch, plugins):
    async def fetch():
        return plugins

    monkeypatch.setattr(mcp_api, "_fetch_plugin_directory", fetch)


def test_skills_marketplace_marks_installed_and_filters_by_facet(monkeypatch, tmp_path):
    _marketplace(monkeypatch, _MARKET_SKILLS)
    _skills_dir(monkeypatch, tmp_path, ["writing"])
    client = build_mcp_client(tmp_path)

    everything = client.get("/skills/marketplace").json()
    by_category = client.get("/skills/marketplace", params={"category": "documentation"}).json()
    by_author = client.get("/skills/marketplace", params={"author": "airtable"}).json()

    assert everything["total"] == 2
    assert everything["authors"] == ["Airtable", "Anthropic"]
    assert everything["categories"] == ["Documentation", "productivity"]
    assert [s["installed"] for s in everything["skills"]] == [True, False]
    assert [s["skill"] for s in by_category["skills"]] == ["writing"]
    assert [s["skill"] for s in by_author["skills"]] == ["bases"]


def test_skills_marketplace_tolerates_a_missing_skills_directory(monkeypatch, tmp_path):
    _marketplace(monkeypatch, _MARKET_SKILLS)
    monkeypatch.setattr(mcp_api, "SKILLS_DIR", tmp_path / "absent")

    payload = build_mcp_client(tmp_path).get("/skills/marketplace").json()

    assert [s["installed"] for s in payload["skills"]] == [False, False]


def test_skills_install_audits_the_admin_and_returns_the_installer_result(tmp_path, monkeypatch):
    events = []

    async def fake_install(plugin, skill):
        return {"status": "installed", "plugin": plugin, "skill": skill}

    monkeypatch.setattr(mcp_api, "install_skill", fake_install)
    client = build_mcp_client(
        tmp_path, append_audit_event=lambda action, **kwargs: events.append((action, kwargs))
    )

    response = client.post("/skills/install", json={"plugin": "docs-kit", "skill": "writing"})

    assert response.json() == {"status": "installed", "plugin": "docs-kit", "skill": "writing"}
    assert events == [(
        "skill_install",
        {"user_email": "admin@example.com", "plugin": "docs-kit", "skill": "writing"},
    )]


def test_skills_list_classifies_each_installed_skill_by_its_attribution(monkeypatch, tmp_path):
    root = _skills_dir(monkeypatch, tmp_path, [
        "anthropic-skill", "local-skill", "no-manifest", "third-party-skill",
    ])
    (root / "loose.txt").write_text("not a skill", encoding="utf-8")
    (root / "anthropic-skill" / "SKILL.md").write_text(
        "<!-- Source: https://github.com/anthropics/claude-plugins-official/tree/main, "
        "Apache-2.0 -->\ndescription: first party\n",
        encoding="utf-8",
    )
    (root / "third-party-skill" / "SKILL.md").write_text(
        "<!-- Source: https://github.com/Airtable/skills, MIT -->\ndescription: vendor\n",
        encoding="utf-8",
    )
    (root / "local-skill" / "SKILL.md").write_text("description: hand written\n", encoding="utf-8")

    payload = build_mcp_client(tmp_path).get("/skills/list").json()

    assert payload == {
        "skills": [
            {"name": "anthropic-skill", "description": "first party", "source": "anthropic"},
            {"name": "local-skill", "description": "hand written", "source": "local"},
            {"name": "third-party-skill", "description": "vendor", "source": "third-party"},
        ],
        "total": 3,
    }


def test_skills_list_is_empty_without_a_skills_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_api, "SKILLS_DIR", tmp_path / "absent")

    assert build_mcp_client(tmp_path).get("/skills/list").json() == {"skills": []}


def test_skills_marketplace_refresh_clears_the_stamp_and_counts_by_author(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_registry, "_SKILLS_MARKETPLACE_FETCHED_AT", "stale-stamp")
    _marketplace(monkeypatch, _MARKET_SKILLS + [dict(_MARKET_SKILLS[0], skill="editing")])

    payload = build_mcp_client(tmp_path).post("/skills/marketplace/refresh").json()

    assert payload == {"status": "ok", "total": 3, "by_author": {"Anthropic": 2, "Airtable": 1}}
    assert mcp_registry._SKILLS_MARKETPLACE_FETCHED_AT is None


def test_plugins_directory_filters_by_category_license_and_free_text(monkeypatch, tmp_path):
    _directory(monkeypatch, _DIRECTORY)
    client = build_mcp_client(tmp_path)

    everything = client.get("/plugins/directory").json()
    by_category = client.get("/plugins/directory", params={"category": "CORE"}).json()
    by_license = client.get("/plugins/directory", params={"license": "mit"}).json()
    by_query = client.get("/plugins/directory", params={"q": "RECORDS"}).json()
    by_author_query = client.get("/plugins/directory", params={"q": "anthropic"}).json()

    assert everything["total"] == 2
    assert everything["categories"] == ["core", "productivity"]
    assert everything["licenses"] == ["Apache-2.0", "MIT"]
    assert [p["name"] for p in by_category["plugins"]] == ["docs-kit"]
    assert [p["name"] for p in by_license["plugins"]] == ["airtable"]
    assert [p["name"] for p in by_query["plugins"]] == ["airtable"]
    assert [p["name"] for p in by_author_query["plugins"]] == ["docs-kit"]


def test_plugins_directory_refresh_clears_the_stamp_and_counts_licenses(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_registry, "_PLUGIN_DIRECTORY_FETCHED_AT", "stale-stamp")
    _directory(monkeypatch, _DIRECTORY + [{"name": "unlicensed"}])

    payload = build_mcp_client(tmp_path).post("/plugins/directory/refresh").json()

    assert payload == {
        "status": "ok",
        "total": 3,
        "by_license": {"Apache-2.0": 1, "MIT": 1, "unknown": 1},
    }
    assert mcp_registry._PLUGIN_DIRECTORY_FETCHED_AT is None


# ── /mcp/call ────────────────────────────────────────────────────────────────


class _Result:
    def as_dict(self):
        return {"status": "ok"}


class _Pipeline:
    def __init__(self):
        self.item = None
        self.user = None

    def ingest(self, item, user_email=None):
        self.item = item
        self.user = user_email
        return _Result()


class _Graph:
    def __init__(self):
        self.calls = []

    def search(self, query, limit, *, allowed_workspaces=None):
        self.calls.append(("search", query, limit, allowed_workspaces))
        return {"matches": []}

    def graph(self, limit, *, allowed_workspaces=None):
        self.calls.append(("graph", limit, allowed_workspaces))
        return {"nodes": [], "edges": []}

    def context_for_query(self, query, limit, *, allowed_workspaces=None):
        self.calls.append(("context", query, limit, allowed_workspaces))
        return "ctx"


class _WorkspaceService:
    def resolve_write_scope(self, requested, user):
        if requested != "org-1":
            raise PermissionError("write scope denied")
        return requested

    def resolve_read_scope(self, requested, user):
        if requested == "org-9":
            raise PermissionError("read scope denied")
        return requested or "personal"


def _call_client(tmp_path, **overrides):
    graph = _Graph()
    pipeline = _Pipeline()
    deps = {
        "knowledge_graph": graph,
        "ingestion_pipeline": pipeline,
        "require_graph": lambda: None,
    }
    deps.update(overrides)
    return build_mcp_client(tmp_path, **deps), graph, pipeline


def test_mcp_call_ingest_without_a_workspace_service_keeps_the_requested_scope(tmp_path):
    client, _graph, pipeline = _call_client(tmp_path)

    response = client.post("/mcp/call", json={
        "action": "knowledge_graph_ingest",
        "args": {"content": "hello", "workspace_id": "team-a", "type": "ai_response"},
    })

    assert response.json() == {"status": "ok"}
    assert pipeline.item.workspace_id == "team-a"
    assert pipeline.item.owner == "alice@example.com"
    assert pipeline.item.metadata["role"] == "assistant"
    assert pipeline.item.metadata["raw"]["user_email"] == "alice@example.com"
    assert pipeline.user == "alice@example.com"


def test_mcp_call_ingest_rejects_a_spoofed_user_email(tmp_path):
    client, _graph, pipeline = _call_client(tmp_path)

    response = client.post("/mcp/call", json={
        "action": "knowledge_graph_ingest",
        "args": {"content": "hello", "user_email": "mallory@example.com"},
    }, headers=EN)

    assert response.status_code == 403
    assert response.json()["detail"] == "user_email must match the authenticated user."
    assert pipeline.item is None


def test_mcp_call_ingest_rejects_a_workspace_the_service_refuses(tmp_path):
    client, _graph, _pipeline = _call_client(tmp_path, workspace_service=_WorkspaceService())

    response = client.post("/mcp/call", json={
        "action": "knowledge_graph_ingest",
        "args": {"content": "hello", "workspace_id": "org-9"},
    })

    assert response.status_code == 403
    assert response.json()["detail"] == "write scope denied"


def test_mcp_call_graph_reads_are_scoped_to_the_callers_workspaces(tmp_path):
    client, graph, _pipeline = _call_client(
        tmp_path, allowed_workspaces_for=lambda user: {"personal", "org-1"}
    )

    searched = client.post("/mcp/call", json={
        "action": "knowledge_graph_search", "args": {"q": "term", "limit": 7},
    })
    graphed = client.post("/mcp/call", json={"action": "knowledge_graph_graph", "args": {}})
    contexted = client.post("/mcp/call", json={
        "action": "knowledge_graph_context", "args": {"query": "term"},
    })

    assert searched.json() == {"matches": []}
    assert graphed.json() == {"nodes": [], "edges": []}
    assert contexted.json() == {"context": "ctx"}
    assert graph.calls == [
        ("search", "term", 7, {"personal", "org-1"}),
        ("graph", 300, {"personal", "org-1"}),
        ("context", "term", 6, {"personal", "org-1"}),
    ]


def test_scoped_knowledge_read_uses_the_workspace_services_read_scope(monkeypatch, tmp_path):
    dispatched = {}
    monkeypatch.setattr(
        mcp_api, "enforce_tool_policy", lambda *args, **kwargs: dispatched.setdefault("policy", args)
    )
    client, _graph, _pipeline = _call_client(
        tmp_path,
        workspace_service=_WorkspaceService(),
        tool_response=lambda _fn, action, args, **kwargs: {"action": action, "args": args},
    )

    allowed = client.post("/mcp/call", json={
        "action": "knowledge_search", "args": {"query": "x", "workspace_id": "org-1"},
    })
    denied = client.post("/mcp/call", json={
        "action": "knowledge_search", "args": {"query": "x", "workspace_id": "org-9"},
    })

    assert allowed.json() == {
        "action": "knowledge_search",
        "args": {"query": "x", "workspace_id": "org-1", "user_email": "alice@example.com"},
    }
    assert denied.status_code == 403
    assert denied.json()["detail"] == "read scope denied"
    assert dispatched["policy"][0] == "knowledge_search"


def test_scoped_knowledge_write_binds_the_caller_without_a_workspace_service(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(mcp_api, "enforce_tool_policy", lambda *args, **kwargs: None)
    client, _graph, _pipeline = _call_client(
        tmp_path, tool_response=lambda _fn, action, args, **kwargs: {"args": args}
    )

    response = client.post("/mcp/call", json={
        "action": "knowledge_save", "args": {"text": "note", "workspace_id": "team-a"},
    })

    assert response.json() == {
        "args": {"text": "note", "workspace_id": "team-a", "user_email": "alice@example.com"}
    }


def test_scoped_knowledge_read_falls_back_to_the_allowed_workspace_list(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_api, "enforce_tool_policy", lambda *args, **kwargs: None)
    client, _graph, _pipeline = _call_client(
        tmp_path,
        allowed_workspaces_for=lambda user: {"personal"},
        tool_response=lambda _fn, action, args, **kwargs: {"args": args},
    )

    defaulted = client.post("/mcp/call", json={"action": "knowledge_tree", "args": {}})
    denied = client.post("/mcp/call", json={
        "action": "knowledge_tree", "args": {"workspace_id": "org-2"},
    }, headers=EN)

    assert defaulted.json()["args"]["workspace_id"] == "personal"
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Workspace 'org-2' is not readable."


def test_scoped_knowledge_call_rejects_a_spoofed_user_email(tmp_path):
    client, _graph, _pipeline = _call_client(tmp_path)

    response = client.post("/mcp/call", json={
        "action": "knowledge_search",
        "args": {"query": "x", "user_email": "mallory@example.com"},
    }, headers=EN)

    assert response.status_code == 403
    assert response.json()["detail"] == "user_email must match the authenticated user."


def test_mcp_call_dispatches_an_auto_approved_tool_through_the_real_policy_gate(tmp_path):
    dispatched = {}

    def tool_response(executor, action, args, **kwargs):
        dispatched["call"] = (executor, action, args, kwargs)
        return {"status": "ok", "action": action}

    client, _graph, _pipeline = _call_client(tmp_path, tool_response=tool_response)

    response = client.post("/mcp/call", json={"action": "todo_read", "args": {}})

    assert response.json() == {"status": "ok", "action": "todo_read"}
    assert dispatched["call"][1] == "todo_read"
    assert dispatched["call"][3] == {"source": "mcp"}


def test_mcp_call_still_blocks_local_file_tools_at_the_real_policy_gate(tmp_path):
    client, _graph, _pipeline = _call_client(tmp_path)

    response = client.post("/mcp/call", json={"action": "local_read", "args": {"path": "/etc/hosts"}})

    assert response.status_code == 403
    assert "local-file approval" in response.json()["detail"]


def test_mcp_call_ingest_accepts_a_workspace_the_service_allows(tmp_path):
    client, _graph, pipeline = _call_client(tmp_path, workspace_service=_WorkspaceService())

    response = client.post("/mcp/call", json={
        "action": "knowledge_graph_ingest",
        "args": {"content": "x", "workspace_id": "org-1", "user_email": "alice@example.com"},
    })

    assert response.status_code == 200
    assert pipeline.item.workspace_id == "org-1"
