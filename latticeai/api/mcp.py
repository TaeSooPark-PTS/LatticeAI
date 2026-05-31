"""MCP / skills / plugins API router.

Extracted from ``server_app.py`` in v1.3.0. Paths and schemas unchanged:
``/mcp/*``, ``/skills/*``, ``/plugins/directory*``, and ``/mcp/call``.

Registry/tool symbols are imported directly from their owning modules
(``mcp_registry``, ``tools``, ``latticeai.core.tool_registry``); server_app-defined
helpers (auth, audit, tool governance/dispatch, KG) are injected, so there is no
import cycle.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import mcp_registry
from mcp_registry import (
    _get_combined_registry,
    _fetch_skills_marketplace,
    _fetch_plugin_directory,
    install_skill,
    SKILLS_DIR,
)
from latticeai.core.tool_registry import MCP_TOOL_DESCRIPTIONS
from tools import AGENT_ROOT, execute_tool


class McpRecommendRequest(BaseModel):
    query: str
    limit: int = 5


class McpInstallRequest(BaseModel):
    mcp_id: str


class McpCustomRequest(BaseModel):
    name: str
    package: str
    description: str = ""
    category: str = "custom"
    icon: str = "🔌"
    env_vars: List[Dict] = []


class SkillInstallRequest(BaseModel):
    plugin: str
    skill: str


class McpCallRequest(BaseModel):
    action: str
    args: Dict = {}


def create_mcp_router(
    *,
    require_user: Callable[[Request], str],
    require_admin: Callable[[Request], Any],
    append_audit_event: Callable[..., None],
    load_mcp_installs: Callable[[], Dict],
    recommend_mcps: Callable[..., Any],
    install_mcp: Callable[..., Any],
    mcp_public_item: Callable[[Dict, Dict], Dict],
    get_tool_permission: Callable[..., Any],
    tool_governance: Dict,
    tool_governance_default: Any,
    check_tool_role: Callable[[str, str], None],
    tool_response: Callable[..., Any],
    require_graph: Callable[[], Any],
    knowledge_graph: Any,
    data_dir: Path,
) -> APIRouter:
    router = APIRouter()

    # Bind injected deps to the names the moved handler bodies expect.
    TOOL_GOVERNANCE = tool_governance
    _TOOL_GOVERNANCE_DEFAULT = tool_governance_default
    _check_tool_role = check_tool_role
    _tool_response = tool_response
    _require_graph = require_graph
    KNOWLEDGE_GRAPH = knowledge_graph

    _CUSTOM_MCP_FILE = data_dir / "custom_mcps.json"

    def _load_custom_mcps() -> List[Dict]:
        if not _CUSTOM_MCP_FILE.exists():
            return []
        try:
            with open(_CUSTOM_MCP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def _save_custom_mcps(items: List[Dict]):
        with open(_CUSTOM_MCP_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    @router.get("/mcp/tools")
    async def mcp_tools():
        installed = load_mcp_installs().get("installed", {})
        registry = await _get_combined_registry()
        tools = []
        for name, description in MCP_TOOL_DESCRIPTIONS.items():
            policy = TOOL_GOVERNANCE.get(name, _TOOL_GOVERNANCE_DEFAULT)
            tools.append({
                "name": name,
                "description": description,
                "permission": get_tool_permission(name),
                "governance": {
                    "risk":         policy["risk"],
                    "destructive":  policy["destructive"],
                    "shell":        policy["shell"],
                    "network":      policy["network"],
                    "auto_approve": policy["auto_approve"],
                    "sandbox":      policy["sandbox"],
                    "rollback":     policy["rollback"],
                },
            })
        return {
            "status": "ok",
            "workspace": str(AGENT_ROOT),
            "installed_mcps": [mcp_public_item(item, installed) for item in registry],
            "tools": tools,
        }

    @router.post("/mcp/recommend")
    async def mcp_recommend(req: McpRecommendRequest, request: Request):
        require_user(request)
        return {"recommendations": await recommend_mcps(req.query, req.limit)}

    @router.post("/mcp/install")
    async def mcp_install(req: McpInstallRequest, request: Request):
        admin_email, _ = require_admin(request)
        append_audit_event("mcp_install", user_email=admin_email, mcp_id=req.mcp_id)
        return await install_mcp(req.mcp_id)

    @router.get("/mcp/installed")
    async def mcp_installed(request: Request):
        require_user(request)
        installed = load_mcp_installs().get("installed", {})
        registry = await _get_combined_registry()
        return {"installed": [mcp_public_item(item, installed) for item in registry]}

    @router.get("/mcp/connectors/{mcp_id}")
    async def mcp_connector(mcp_id: str, request: Request):
        require_user(request)
        registry = await _get_combined_registry()
        item = next((e for e in registry if e["id"] == mcp_id), None)
        if not item or item.get("install_mode") != "connector":
            raise HTTPException(status_code=404, detail="커넥터를 찾을 수 없습니다.")
        installed = load_mcp_installs().get("installed", {})
        public = mcp_public_item(item, installed)
        public["instructions"] = [
            "Codex 또는 ChatGPT 앱의 Connectors 설정을 엽니다.",
            f"{item['name']} 항목을 선택하고 계정을 인증합니다.",
            "인증 후 Lattice AI에서 이 MCP를 다시 활성화하면 작업에 사용할 수 있습니다.",
        ]
        return public

    @router.post("/mcp/registry/refresh")
    async def mcp_registry_refresh(request: Request):
        require_user(request)
        mcp_registry._REMOTE_REGISTRY_FETCHED_AT = None
        registry = await _get_combined_registry()
        return {"status": "ok", "total": len(registry), "remote": len(mcp_registry._REMOTE_REGISTRY_CACHE)}

    @router.get("/mcp/claude-code-servers")
    async def mcp_claude_code_servers(request: Request):
        """Read ~/.claude/settings.json mcpServers and return them as Lattice MCP items."""
        require_user(request)
        settings_path = Path.home() / ".claude" / "settings.json"
        if not settings_path.exists():
            return {"servers": []}
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
            mcp_servers = settings.get("mcpServers", {})
            servers = []
            for name, cfg in mcp_servers.items():
                cmd = cfg.get("command", "")
                args = cfg.get("args", [])
                package = " ".join([cmd] + args) if args else cmd
                env = cfg.get("env", {})
                env_vars = [{"name": k, "value": v} for k, v in env.items()]
                servers.append({
                    "id": f"claude-code:{name}",
                    "name": name,
                    "description": f"Claude Code MCP: {package}",
                    "package": package,
                    "icon": "🤖",
                    "category": "Claude Code",
                    "source": "claude-code",
                    "installed": True,
                    "env_vars": env_vars,
                })
            return {"servers": servers}
        except Exception as e:
            logging.warning("mcp_claude_code_servers failed: %s", e)
            return {"servers": []}

    @router.get("/mcp/custom")
    async def mcp_custom_list(request: Request):
        """Return user-added custom MCP entries."""
        require_user(request)
        return {"custom": _load_custom_mcps()}

    @router.post("/mcp/custom")
    async def mcp_custom_add(req: McpCustomRequest, request: Request):
        """Save a custom MCP entry (admin-only)."""
        admin_email, _ = require_admin(request)
        append_audit_event("mcp_custom_add", user_email=admin_email, name=req.name, package=req.package)
        if not req.name.strip():
            raise HTTPException(status_code=400, detail="name은 필수입니다.")
        if not req.package.strip():
            raise HTTPException(status_code=400, detail="package는 필수입니다.")
        items = _load_custom_mcps()
        entry = {
            "id": f"custom:{req.name.strip().lower().replace(' ', '-')}",
            "name": req.name.strip(),
            "package": req.package.strip(),
            "description": req.description.strip(),
            "category": req.category or "custom",
            "icon": req.icon or "🔌",
            "env_vars": req.env_vars or [],
            "install_mode": "npm",
            "source": "custom",
            "installed": False,
            "added_at": datetime.now().isoformat(),
        }
        items = [e for e in items if e["id"] != entry["id"]]
        items.append(entry)
        _save_custom_mcps(items)
        return {"status": "ok", "entry": entry}

    @router.delete("/mcp/custom/{mcp_id:path}")
    async def mcp_custom_delete(mcp_id: str, request: Request):
        """Remove a custom MCP entry."""
        require_admin(request)
        items = _load_custom_mcps()
        before = len(items)
        items = [e for e in items if e["id"] != mcp_id]
        if len(items) == before:
            raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
        _save_custom_mcps(items)
        return {"status": "ok"}

    # ── Skills & Plugin Directory ─────────────────────────────────────────

    @router.get("/skills/marketplace")
    async def skills_marketplace(request: Request, category: Optional[str] = None, author: Optional[str] = None):
        require_user(request)
        skills = await _fetch_skills_marketplace()
        installed_names = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()} if SKILLS_DIR.exists() else set()
        filtered = skills
        if category:
            filtered = [s for s in filtered if s.get("category", "").lower() == category.lower()]
        if author:
            filtered = [s for s in filtered if s.get("author", "").lower() == author.lower()]
        return {
            "skills": [{**s, "installed": s["skill"] in installed_names} for s in filtered],
            "total": len(filtered),
            "authors": sorted({s["author"] for s in skills}),
            "categories": sorted({s["category"] for s in skills}),
        }

    @router.post("/skills/install")
    async def skills_install(req: SkillInstallRequest, request: Request):
        admin_email, _ = require_admin(request)
        append_audit_event("skill_install", user_email=admin_email, plugin=req.plugin, skill=req.skill)
        return await install_skill(req.plugin, req.skill)

    @router.get("/skills/list")
    async def skills_list(request: Request):
        require_user(request)
        if not SKILLS_DIR.exists():
            return {"skills": []}
        skills = []
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            lines = skill_md.read_text(encoding="utf-8").splitlines()
            desc = next((l.split(":", 1)[1].strip() for l in lines if l.startswith("description:")), "")
            comment = lines[0] if lines else ""
            if "anthropics/claude-plugins-official" in comment:
                source = "anthropic"
            elif "Source:" in comment:
                source = "third-party"
            else:
                source = "local"
            skills.append({"name": skill_dir.name, "description": desc, "source": source})
        return {"skills": skills, "total": len(skills)}

    @router.post("/skills/marketplace/refresh")
    async def skills_marketplace_refresh(request: Request):
        require_user(request)
        mcp_registry._SKILLS_MARKETPLACE_FETCHED_AT = None
        skills = await _fetch_skills_marketplace()
        by_author = {}
        for s in skills:
            by_author[s["author"]] = by_author.get(s["author"], 0) + 1
        return {"status": "ok", "total": len(skills), "by_author": by_author}

    @router.get("/plugins/directory")
    async def plugins_directory(
        request: Request,
        category: Optional[str] = None,
        license: Optional[str] = None,
        q: Optional[str] = None,
    ):
        require_user(request)
        plugins = await _fetch_plugin_directory()
        filtered = plugins
        if category:
            filtered = [p for p in filtered if p.get("category", "").lower() == category.lower()]
        if license:
            filtered = [p for p in filtered if p.get("license", "").lower() == license.lower()]
        if q:
            q_lower = q.lower()
            filtered = [
                p for p in filtered
                if q_lower in p.get("name", "").lower()
                or q_lower in p.get("description", "").lower()
                or q_lower in p.get("author", "").lower()
            ]
        return {
            "plugins": filtered,
            "total": len(filtered),
            "categories": sorted({p["category"] for p in plugins if p.get("category")}),
            "licenses": sorted({p["license"] for p in plugins if p.get("license")}),
        }

    @router.post("/plugins/directory/refresh")
    async def plugins_directory_refresh(request: Request):
        require_user(request)
        mcp_registry._PLUGIN_DIRECTORY_FETCHED_AT = None
        plugins = await _fetch_plugin_directory()
        by_license = {}
        for p in plugins:
            lic = p.get("license", "unknown")
            by_license[lic] = by_license.get(lic, 0) + 1
        return {"status": "ok", "total": len(plugins), "by_license": by_license}

    @router.post("/mcp/call")
    async def mcp_call(req: McpCallRequest, request: Request):
        current_user = require_user(request)
        args = req.args or {}
        if req.action == "knowledge_graph_ingest":
            _require_graph()
            return KNOWLEDGE_GRAPH.ingest_message(
                args.get("role") or ("assistant" if args.get("type") == "ai_response" else "user"),
                args.get("content") or "",
                user_email=args.get("user_email") or current_user,
                user_nickname=args.get("user_nickname"),
                source=args.get("source") or "mcp",
                conversation_id=args.get("conversation_id"),
                raw=args,
            )
        if req.action == "knowledge_graph_search":
            _require_graph()
            return KNOWLEDGE_GRAPH.search(args.get("query") or args.get("q") or "", args.get("limit", 30))
        if req.action == "knowledge_graph_graph":
            _require_graph()
            return KNOWLEDGE_GRAPH.graph(args.get("limit", 300))
        if req.action == "knowledge_graph_context":
            _require_graph()
            return {
                "context": KNOWLEDGE_GRAPH.context_for_query(
                    args.get("query") or args.get("q") or "",
                    args.get("limit", 6),
                )
            }
        _check_tool_role(req.action, current_user)
        return _tool_response(execute_tool, req.action, req.args or {})

    return router
