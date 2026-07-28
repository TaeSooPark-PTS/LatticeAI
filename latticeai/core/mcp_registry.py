"""
Lattice AI — MCP Registry data & pure helper functions.

Extracted from server.py to reduce module size.
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx
from fastapi import HTTPException

# ── MCP Registry (built-in tool definitions) ─────────────────────────────────
# Built-in MCP server catalog data → .mcp_catalog (re-exported).
from .mcp_catalog import MCP_REGISTRY  # noqa: F401

# ── Remote MCP Registry (registry.modelcontextprotocol.io) ───────────────────
_REMOTE_REGISTRY_CACHE: List[Dict] = []
_REMOTE_REGISTRY_FETCHED_AT: Optional[datetime] = None
_REMOTE_REGISTRY_TTL = timedelta(hours=1)
_REMOTE_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
_LOCAL_IDS = {e["id"] for e in MCP_REGISTRY}

async def _fetch_remote_mcp_registry() -> List[Dict]:
    global _REMOTE_REGISTRY_CACHE, _REMOTE_REGISTRY_FETCHED_AT
    now = datetime.now()
    if _REMOTE_REGISTRY_FETCHED_AT and (now - _REMOTE_REGISTRY_FETCHED_AT) < _REMOTE_REGISTRY_TTL:
        return _REMOTE_REGISTRY_CACHE
    try:
        result: List[Dict] = []
        cursor = None
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                params: Dict = {"limit": 100}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get(_REMOTE_REGISTRY_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                for s in data.get("servers", []):
                    srv = s["server"]
                    meta = s.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {})
                    if not meta.get("isLatest", True):
                        continue
                    pkg = next(
                        (p for p in srv.get("packages", [])
                         if p.get("transport", {}).get("type") == "stdio"
                         and p.get("registryType") in ("npm", "pypi")),
                        None,
                    )
                    if not pkg:
                        continue
                    entry_id = srv["name"].replace("/", "-").replace(".", "-")
                    if entry_id in _LOCAL_IDS:
                        continue
                    result.append({
                        "id": entry_id,
                        "name": srv.get("title") or srv["name"],
                        "category": "MCP Registry",
                        "install_mode": pkg["registryType"],
                        "package": pkg["identifier"],
                        "package_version": pkg.get("version"),
                        "description": srv.get("description", ""),
                        "keywords": [],
                        "capabilities": [],
                        "source": "registry",
                        "homepage": (srv.get("repository") or {}).get("url"),
                    })
                cursor = data.get("nextCursor")
                if not cursor:
                    break
        _REMOTE_REGISTRY_CACHE = result
        _REMOTE_REGISTRY_FETCHED_AT = now
        logging.info("Fetched %d stdio MCP servers from remote registry", len(result))
    except Exception as e:
        logging.warning("Failed to fetch remote MCP registry: %s", e)
    return _REMOTE_REGISTRY_CACHE

async def _get_combined_registry() -> List[Dict]:
    remote = await _fetch_remote_mcp_registry()
    return MCP_REGISTRY + remote

# ── Anthropic Skills Marketplace (Apache 2.0) ─────────────────────────────────
_MARKETPLACE_RAW = "https://raw.githubusercontent.com/anthropics/claude-plugins-official/main"
_MARKETPLACE_API = "https://api.github.com/repos/anthropics/claude-plugins-official/contents"

# 검증된 서드파티 skills 소스 (Apache-2.0 / MIT)
_THIRD_PARTY_SKILL_SOURCES: List[Dict] = [
    {
        "plugin": "adobe-for-creativity", "author": "Adobe", "license": "Apache-2.0",
        "repo": "adobe/skills", "branch": "main",
        "plugin_path": "plugins/creative-cloud/adobe-for-creativity",
        "category": "design",
    },
    {
        "plugin": "airtable", "author": "Airtable", "license": "MIT",
        "repo": "Airtable/skills", "branch": "main",
        "plugin_path": "plugins/airtable",
        "category": "productivity",
    },
    {
        "plugin": "auth0", "author": "Auth0", "license": "Apache-2.0",
        "repo": "auth0/agent-skills", "branch": "main",
        "plugin_path": "plugins/auth0",
        "category": "security",
    },
    {
        "plugin": "expo", "author": "Expo", "license": "MIT",
        "repo": "expo/skills", "branch": "main",
        "plugin_path": "plugins/expo",
        "category": "development",
    },
    {
        "plugin": "logfire", "author": "Pydantic", "license": "MIT",
        "repo": "pydantic/skills", "branch": "main",
        "plugin_path": "plugins/logfire",
        "category": "monitoring",
    },
]

# 검증된 레포 라이선스 맵 (GitHub API 없이 빠르게 조회)
_KNOWN_REPO_LICENSES: Dict[str, str] = {
    # Apache-2.0
    "adobe/skills": "Apache-2.0", "awslabs/agent-plugins": "Apache-2.0",
    "auth0/agent-skills": "Apache-2.0", "aws/agent-toolkit-for-aws": "Apache-2.0",
    "carta/plugins": "Apache-2.0", "circlefin/skills": "Apache-2.0",
    "clickhouse/clickhouse-docs": "Apache-2.0", "cloudflare/agents": "Apache-2.0",
    "cockroachdb/claude-code": "Apache-2.0", "codspeed-hq/codspeed-claude": "Apache-2.0",
    "DataDog/datadog-claude-code": "Apache-2.0", "datahub-project/datahub-skills": "Apache-2.0",
    "neondatabase/agent-skills": "Apache-2.0", "PagerDuty/pd-ai-agents-plugins": "Apache-2.0",
    "getpostman/postman-mcp-server": "Apache-2.0", "qdrant/qdrant-skills": "Apache-2.0",
    "rootlyhq/rootly-plugins": "Apache-2.0", "snowflake-labs/snowflake-claude": "Apache-2.0",
    "sumup/sumup-claude": "Apache-2.0", "zilliz-labs/zilliz-skills": "Apache-2.0",
    "mercadopago/mercadopago-claude-marketplace": "Apache-2.0",
    # MIT
    "Airtable/skills": "MIT", "endorlabs/ai-plugins": "MIT",
    "apollographql/apollo-claude-skills": "MIT", "appwrite/skills": "MIT",
    "atlan-inc/claude-code-skills": "MIT", "boxer/boxerbox": "MIT",
    "buildkite/claude-code": "MIT", "coderabbitai/coderabbit-skills": "MIT",
    "CrowdStrike/crowdstrike-skills": "MIT", "microsoft/Dataverse-skills": "MIT",
    "duckdb/duckdb-skills": "MIT", "expo/skills": "MIT",
    "intercom/intercom-skills": "MIT", "pydantic/skills": "MIT",
    "mapbox/mapbox-skills": "MIT", "mintlify/mintlify-skills": "MIT",
    "miroapp/miro-ai": "MIT", "netlify/netlify-skills": "MIT",
    "pinecone-io/pinecone-skills": "MIT", "railwayapp/railway-skills": "MIT",
    "resend/resend-skills": "MIT", "sanity-io/sanity-skills": "MIT",
    "getsentry/sentry-ai-skills": "MIT", "Shopify/liquid-skills": "MIT",
    "slackapi/slack-skills": "MIT", "stripe/stripe-skills": "MIT",
    "twilio-labs/twilio-skills": "MIT", "workos/workos-skills": "MIT",
    "zoom/zoom-skills": "MIT", "aws-samples/sample-claude-code-plugins-for-startups": "MIT-0",
}

_SKILLS_MARKETPLACE_CACHE: List[Dict] = []
_SKILLS_MARKETPLACE_FETCHED_AT: Optional[datetime] = None
_SKILLS_MARKETPLACE_TTL = timedelta(hours=1)

def _extract_skill_desc(skill_md: str, fallback: str) -> str:
    for line in skill_md.splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return fallback

async def _fetch_plugin_skills(client: httpx.AsyncClient, source: Dict) -> List[Dict]:
    """단일 소스에서 skill 목록을 fetch해 반환"""
    repo, branch, plugin_path = source["repo"], source["branch"], source["plugin_path"]
    raw_base = f"https://raw.githubusercontent.com/{repo}/{branch}"
    api_base = f"https://api.github.com/repos/{repo}/contents"
    homepage_base = f"https://github.com/{repo}/tree/{branch}"

    dir_resp = await client.get(f"{api_base}/{plugin_path}/skills")
    if dir_resp.status_code != 200:
        return []
    skill_dirs = [f["name"] for f in dir_resp.json() if f["type"] == "dir"]

    skills: List[Dict] = []
    for skill_name in skill_dirs:
        skill_md_url = f"{raw_base}/{plugin_path}/skills/{skill_name}/SKILL.md"
        sm_resp = await client.get(skill_md_url)
        if sm_resp.status_code != 200:
            continue
        skills.append({
            "plugin":       source["plugin"],
            "skill":        skill_name,
            "category":     source.get("category", "development"),
            "description":  _extract_skill_desc(sm_resp.text, source.get("description", "")),
            "skill_md_url": skill_md_url,
            "homepage":     f"{homepage_base}/{plugin_path}/skills/{skill_name}",
            "license":      source["license"],
            "author":       source["author"],
        })
    return skills

async def _fetch_skills_marketplace() -> List[Dict]:
    global _SKILLS_MARKETPLACE_CACHE, _SKILLS_MARKETPLACE_FETCHED_AT
    now = datetime.now()
    if _SKILLS_MARKETPLACE_FETCHED_AT and (now - _SKILLS_MARKETPLACE_FETCHED_AT) < _SKILLS_MARKETPLACE_TTL:
        return _SKILLS_MARKETPLACE_CACHE
    try:
        result: List[Dict] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            # ── Anthropic 공식 skills (Apache-2.0) ──────────────────────────
            mp_resp = await client.get(f"{_MARKETPLACE_RAW}/.claude-plugin/marketplace.json")
            mp_resp.raise_for_status()
            marketplace_json = mp_resp.json()
            anthropic_plugins = [
                p for p in marketplace_json.get("plugins", [])
                if (p.get("author") or {}).get("name") == "Anthropic"
                and isinstance(p.get("source"), str)
                and p["source"].startswith("./")
            ]
            for plugin in anthropic_plugins:
                plugin_path = plugin["source"].lstrip("./")
                result.extend(await _fetch_plugin_skills(client, {
                    "plugin":      plugin["name"],
                    "author":      "Anthropic",
                    "license":     "Apache-2.0",
                    "repo":        "anthropics/claude-plugins-official",
                    "branch":      "main",
                    "plugin_path": plugin_path,
                    "category":    plugin.get("category", "development"),
                    "description": plugin.get("description", ""),
                }))
            # ── 검증된 서드파티 skills ────────────────────────────────────────
            for source in _THIRD_PARTY_SKILL_SOURCES:
                result.extend(await _fetch_plugin_skills(client, source))

        _SKILLS_MARKETPLACE_CACHE = result
        _SKILLS_MARKETPLACE_FETCHED_AT = now
        logging.info("Fetched %d skills from marketplace (%d sources)",
                     len(result), len(anthropic_plugins) + len(_THIRD_PARTY_SKILL_SOURCES))
    except Exception as e:
        logging.warning("Failed to fetch skills marketplace: %s", e)
    return _SKILLS_MARKETPLACE_CACHE

# ── Plugin Directory ──────────────────────────────────────────────────────────
_PLUGIN_DIRECTORY_CACHE: List[Dict] = []
_PLUGIN_DIRECTORY_FETCHED_AT: Optional[datetime] = None
_PLUGIN_DIRECTORY_TTL = timedelta(hours=1)
_OPEN_LICENSES = {"Apache-2.0", "MIT", "MIT-0", "CC-BY-4.0"}
_REPO_LICENSE_CACHE: Dict[str, str] = {}

async def _get_repo_license(client: httpx.AsyncClient, repo: str) -> str:
    if repo in _REPO_LICENSE_CACHE:
        return _REPO_LICENSE_CACHE[repo]
    if repo in _KNOWN_REPO_LICENSES:
        _REPO_LICENSE_CACHE[repo] = _KNOWN_REPO_LICENSES[repo]
        return _KNOWN_REPO_LICENSES[repo]
    try:
        r = await client.get(f"https://api.github.com/repos/{repo}", timeout=5.0)
        lic = (r.json().get("license") or {}).get("spdx_id", "") if r.status_code == 200 else ""
    except Exception:
        lic = ""
    _REPO_LICENSE_CACHE[repo] = lic
    return lic

async def _fetch_plugin_directory() -> List[Dict]:
    global _PLUGIN_DIRECTORY_CACHE, _PLUGIN_DIRECTORY_FETCHED_AT
    now = datetime.now()
    if _PLUGIN_DIRECTORY_FETCHED_AT and (now - _PLUGIN_DIRECTORY_FETCHED_AT) < _PLUGIN_DIRECTORY_TTL:
        return _PLUGIN_DIRECTORY_CACHE
    try:
        result: List[Dict] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            mp_resp = await client.get(f"{_MARKETPLACE_RAW}/.claude-plugin/marketplace.json")
            mp_resp.raise_for_status()
            plugins = mp_resp.json().get("plugins", [])

            for p in plugins:
                author = (p.get("author") or {}).get("name", "")
                src = p.get("source", {})

                # Anthropic 같은 레포 플러그인 → Apache-2.0 확인됨
                if isinstance(src, str) and src.startswith("./") and author == "Anthropic":
                    plugin_path = src.lstrip("./")
                    result.append({
                        "name":        p["name"],
                        "description": p.get("description", ""),
                        "category":    p.get("category", ""),
                        "author":      author,
                        "license":     "Apache-2.0",
                        "homepage":    p.get("homepage") or f"https://github.com/anthropics/claude-plugins-official/tree/main/{plugin_path}",
                        "source_type": "anthropic",
                    })
                    continue

                # 외부 레포 플러그인 → 라이선스 확인
                if not isinstance(src, dict):
                    continue
                repo_url = src.get("url", "").replace("https://github.com/", "").replace(".git", "").split("/tree/")[0]
                if not repo_url:
                    continue
                license_id = await _get_repo_license(client, repo_url)
                if license_id not in _OPEN_LICENSES:
                    continue
                result.append({
                    "name":        p["name"],
                    "description": p.get("description", ""),
                    "category":    p.get("category", ""),
                    "author":      author or repo_url.split("/")[0],
                    "license":     license_id,
                    "homepage":    p.get("homepage") or f"https://github.com/{repo_url}",
                    "source_type": "third-party",
                })

        _PLUGIN_DIRECTORY_CACHE = result
        _PLUGIN_DIRECTORY_FETCHED_AT = now
        logging.info("Fetched plugin directory: %d open-source plugins", len(result))
    except Exception as e:
        logging.warning("Failed to fetch plugin directory: %s", e)
    return _PLUGIN_DIRECTORY_CACHE

# ─────────────────────────────────────────────────────────────────────────────

SKILLS_DIR = Path(__file__).resolve().parent / "skills"

async def install_skill(plugin: str, skill: str) -> Dict:
    marketplace = await _fetch_skills_marketplace()
    entry = next((s for s in marketplace if s["plugin"] == plugin and s["skill"] == skill), None)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Skill '{plugin}/{skill}' not found in marketplace")
    skill_dir = SKILLS_DIR / skill
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md_path = skill_dir / "SKILL.md"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(entry["skill_md_url"])
        resp.raise_for_status()
        content = resp.text
    # 출처 표기 (Apache-2.0 / MIT 공통)
    repo_hint = entry.get("homepage", "")
    attribution = f"<!-- Source: {repo_hint}, {entry['license']} -->\n"
    if not content.startswith("<!--"):
        content = attribution + content
    skill_md_path.write_text(content, encoding="utf-8")
    risk_path = skill_dir / "risk.json"
    if not risk_path.exists():
        risk_path.write_text(json.dumps({
            "risk": "read", "destructive": False,
            "shell": False, "network": False,
            "auto_approve": True, "sandbox": "workspace", "rollback": "none"
        }, indent=2), encoding="utf-8")
    return {
        "status":  "installed",
        "plugin":  plugin,
        "skill":   skill,
        "path":    str(skill_dir),
        "license": entry["license"],
        "author":  entry["author"],
    }


def create_mcp_install_state(data_dir: Path) -> Dict[str, Callable]:
    """Return bound MCP state helpers for given data_dir. No global side effects."""
    MCP_FILE = Path(data_dir) / "mcp_installs.json"

    def load_mcp_installs() -> Dict:
        if not os.path.exists(MCP_FILE):
            return {"installed": {}, "updated_at": None}
        try:
            with open(MCP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if "installed" not in data:
                data["installed"] = {}
            return data
        except Exception as e:
            logging.warning("load_mcp_installs failed: %s", e)
            return {"installed": {}, "updated_at": None}

    def save_mcp_installs(data: Dict) -> None:
        data["updated_at"] = datetime.now().isoformat()
        with open(MCP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def mcp_public_item(item: Dict, installed_state: Dict) -> Dict:
        state = installed_state.get(item.get("id"), {}) or {}
        installed = item.get("install_mode") in {"builtin", "bundled"} or bool(state.get("installed"))
        connector_pending = item.get("install_mode") == "connector" and not state.get("authenticated")
        authenticated = item.get("install_mode") != "connector" or bool(state.get("authenticated"))
        return {
            "id": item.get("id"),
            "name": item.get("name"),
            "category": item.get("category", ""),
            "install_mode": item.get("install_mode"),
            "description": item.get("description", ""),
            "capabilities": item.get("capabilities", []),
            "connector_url": item.get("connector_url"),
            "external_url": item.get("external_url"),
            "package": item.get("package"),
            "homepage": item.get("homepage"),
            "source": item.get("source", "local"),
            "installed": installed,
            "status": state.get("status") or ("active" if installed and not connector_pending else "needs_auth" if connector_pending else "available"),
            "authenticated": authenticated,
            "updated_at": state.get("updated_at"),
        }

    async def recommend_mcps(query: str, limit: int = 5) -> List[Dict]:
        text = (query or "").lower()
        installed = load_mcp_installs().get("installed", {})
        registry = await _get_combined_registry()
        scored: List[Dict] = []
        for item in registry:
            score = 0
            hits: List[str] = []
            for keyword in item.get("keywords", []):
                if keyword.lower() in text:
                    score += 3 if len(keyword) > 2 else 1
                    hits.append(keyword)
            if not hits and text:
                desc_words = item.get("description", "").lower().split()
                for word in text.split():
                    if len(word) > 2 and word in desc_words:
                        score += 1
                        hits.append(word)
            if item.get("id") == "filesystem" and any(w in text for w in ["만들", "구현", "build", "deploy", "코드", "앱"]):
                score += 2
            if score:
                public = mcp_public_item(item, installed)
                public["score"] = score
                public["matched_keywords"] = hits[:6]
                scored.append(public)
        if not scored:
            fallback_ids = ["filesystem", "browser", "documents"]
            scored = [
                {**mcp_public_item(item, installed), "score": 1, "matched_keywords": []}
                for item in registry if item.get("id") in fallback_ids
            ]
        return sorted(scored, key=lambda x: x["score"], reverse=True)[:max(1, min(limit, 24))]

    async def install_mcp(mcp_id: str) -> Dict:
        registry = await _get_combined_registry()
        item = next((entry for entry in registry if entry.get("id") == mcp_id), None)
        if not item:
            raise HTTPException(status_code=404, detail="MCP를 찾을 수 없습니다.")
        data = load_mcp_installs()
        state = data.setdefault("installed", {})
        status = "active"
        message = "MCP가 활성화되었습니다."
        if item.get("install_mode") == "connector":
            status = "needs_auth"
            message = "커넥터 인증이 필요합니다. Codex 앱의 connector 설정에서 계정을 연결하면 바로 사용할 수 있습니다."
        elif item.get("install_mode") == "pip":
            packages = item.get("pip_packages") or []
            for pkg in packages:
                completed = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", pkg],
                    capture_output=True, text=True, timeout=900, check=False,
                )
                if completed.returncode != 0:
                    raise HTTPException(status_code=500, detail=(completed.stderr or "")[-2000:] or f"{pkg} 설치 실패")
            message = f"필수 패키지 설치 완료: {', '.join(packages)}"
        elif item.get("install_mode") == "pypi":
            pkg = item.get("package", "")
            version = item.get("package_version")
            pkg_str = f"{pkg}=={version}" if version else pkg
            completed = subprocess.run([sys.executable, "-m", "pip", "install", pkg_str], capture_output=True, text=True, timeout=300, check=False)
            if completed.returncode != 0:
                raise HTTPException(status_code=500, detail=(completed.stderr or "")[-2000:] or f"{pkg} 설치 실패")
            message = f"pip 패키지 설치 완료: {pkg_str}"
        elif item.get("install_mode") == "npm":
            pkg = item.get("package", "")
            version = item.get("package_version")
            pkg_str = f"{pkg}@{version}" if version else pkg
            completed = subprocess.run(["npm", "install", "-g", pkg_str], capture_output=True, text=True, timeout=300, check=False)
            if completed.returncode != 0:
                raise HTTPException(status_code=500, detail=(completed.stderr or "")[-2000:] or f"{pkg} 설치 실패")
            message = f"npm 패키지 설치 완료: {pkg_str}"
        state[mcp_id] = {
            "installed": True,
            "status": status,
            "authenticated": item.get("install_mode") != "connector",
            "updated_at": datetime.now().isoformat(),
        }
        save_mcp_installs(data)
        public = mcp_public_item(item, state)
        public["message"] = message
        return public

    return {
        "load_mcp_installs": load_mcp_installs,
        "save_mcp_installs": save_mcp_installs,
        "mcp_public_item": mcp_public_item,
        "recommend_mcps": recommend_mcps,
        "install_mcp": install_mcp,
    }
