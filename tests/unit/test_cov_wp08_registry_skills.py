"""Skills marketplace fetchers and ``install_skill``.

Uses the fake ``httpx`` module defined next door in
``test_cov_wp08_registry_remote`` so the marketplace never touches the network,
and redirects ``SKILLS_DIR`` at ``tmp_path`` so nothing is written into the repo.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest
from fastapi import HTTPException

from latticeai.core import mcp_registry
from tests.unit.test_cov_wp08_registry_remote import (
    FakeAsyncClient,
    FakeHttpx,
    FakeResponse,
)

_MARKETPLACE_URL = mcp_registry._MARKETPLACE_RAW + "/.claude-plugin/marketplace.json"
_ANTHROPIC_DIR = (
    "https://api.github.com/repos/anthropics/claude-plugins-official"
    "/contents/plugins/docs-kit/skills"
)
_ANTHROPIC_RAW = (
    "https://raw.githubusercontent.com/anthropics/claude-plugins-official/main"
    "/plugins/docs-kit/skills"
)
_AIRTABLE_DIR = "https://api.github.com/repos/Airtable/skills/contents/plugins/airtable/skills"
_AIRTABLE_RAW = "https://raw.githubusercontent.com/Airtable/skills/main/plugins/airtable/skills"


def _reset_marketplace(monkeypatch, *, cache=None, fetched_at=None):
    monkeypatch.setattr(
        mcp_registry, "_SKILLS_MARKETPLACE_CACHE", [] if cache is None else cache
    )
    monkeypatch.setattr(mcp_registry, "_SKILLS_MARKETPLACE_FETCHED_AT", fetched_at)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("name: writing\ndescription: Write docs well\nversion: 1\n", "Write docs well"),
        ("name: writing\nno front matter here\n", "fallback text"),
    ],
)
def test_extract_skill_desc_reads_front_matter_or_falls_back(body, expected):
    assert mcp_registry._extract_skill_desc(body, "fallback text") == expected


def test_fetch_plugin_skills_skips_files_and_unreadable_skill_markdown():
    def handler(url, params):
        if url == _AIRTABLE_DIR:
            return FakeResponse([
                {"name": "bases", "type": "dir"},
                {"name": "gone", "type": "dir"},
                {"name": "README.md", "type": "file"},
            ])
        if url == _AIRTABLE_RAW + "/bases/SKILL.md":
            return FakeResponse(text="description: Manage bases\n")
        return FakeResponse(status_code=404)

    client = FakeAsyncClient(handler, [], {})
    source = {
        "plugin": "airtable",
        "author": "Airtable",
        "license": "MIT",
        "repo": "Airtable/skills",
        "branch": "main",
        "plugin_path": "plugins/airtable",
        "category": "productivity",
    }

    skills = asyncio.run(mcp_registry._fetch_plugin_skills(client, source))

    assert skills == [{
        "plugin": "airtable",
        "skill": "bases",
        "category": "productivity",
        "description": "Manage bases",
        "skill_md_url": _AIRTABLE_RAW + "/bases/SKILL.md",
        "homepage": "https://github.com/Airtable/skills/tree/main/plugins/airtable/skills/bases",
        "license": "MIT",
        "author": "Airtable",
    }]


def test_fetch_plugin_skills_returns_empty_when_the_directory_is_missing():
    def handler(url, params):
        return FakeResponse(status_code=404)

    client = FakeAsyncClient(handler, [], {})
    source = {
        "plugin": "ghost",
        "author": "Nobody",
        "license": "MIT",
        "repo": "ghost/skills",
        "branch": "main",
        "plugin_path": "plugins/ghost",
    }

    assert asyncio.run(mcp_registry._fetch_plugin_skills(client, source)) == []


_MARKETPLACE_PAYLOAD = {
    "plugins": [
        {
            "name": "docs-kit",
            "source": "./plugins/docs-kit",
            "author": {"name": "Anthropic"},
            "category": "documentation",
            "description": "Docs plugin",
        },
        # Not Anthropic, and not a same-repo source: excluded from the first-party pass.
        {"name": "vendor-kit", "source": {"url": "https://github.com/x/y"}, "author": {"name": "X"}},
    ]
}


def _marketplace_handler(url, params):
    if url == _MARKETPLACE_URL:
        return FakeResponse(_MARKETPLACE_PAYLOAD)
    if url == _ANTHROPIC_DIR:
        return FakeResponse([
            {"name": "writing", "type": "dir"},
            {"name": "unreadable", "type": "dir"},
        ])
    if url == _ANTHROPIC_RAW + "/writing/SKILL.md":
        return FakeResponse(text="description: Write docs well\n")
    if url == _AIRTABLE_DIR:
        return FakeResponse([{"name": "bases", "type": "dir"}])
    if url == _AIRTABLE_RAW + "/bases/SKILL.md":
        return FakeResponse(text="description: Manage bases\n")
    return FakeResponse(status_code=404)


def test_skills_marketplace_merges_first_party_and_vetted_third_party_sources(monkeypatch):
    _reset_marketplace(monkeypatch)
    monkeypatch.setattr(mcp_registry, "httpx", FakeHttpx(_marketplace_handler))

    skills = asyncio.run(mcp_registry._fetch_skills_marketplace())

    assert [(s["plugin"], s["skill"], s["author"]) for s in skills] == [
        ("docs-kit", "writing", "Anthropic"),
        ("airtable", "bases", "Airtable"),
    ]
    assert skills[0]["license"] == "Apache-2.0"
    assert skills[0]["category"] == "documentation"
    assert skills[1]["license"] == "MIT"
    assert mcp_registry._SKILLS_MARKETPLACE_CACHE == skills
    assert mcp_registry._SKILLS_MARKETPLACE_FETCHED_AT is not None


def test_skills_marketplace_serves_cache_inside_ttl(monkeypatch):
    cached = [{"plugin": "cached", "skill": "cached"}]
    _reset_marketplace(monkeypatch, cache=cached, fetched_at=datetime.now())

    def explode(url, params):
        raise AssertionError("a cache hit must not reach the network")

    monkeypatch.setattr(mcp_registry, "httpx", FakeHttpx(explode))

    assert asyncio.run(mcp_registry._fetch_skills_marketplace()) == cached


def test_skills_marketplace_failure_keeps_the_previous_cache(monkeypatch):
    previous = [{"plugin": "previous", "skill": "previous"}]
    _reset_marketplace(monkeypatch, cache=previous)

    def handler(url, params):
        return FakeResponse({}, status_code=502, error=RuntimeError("502 marketplace"))

    monkeypatch.setattr(mcp_registry, "httpx", FakeHttpx(handler))

    assert asyncio.run(mcp_registry._fetch_skills_marketplace()) == previous
    assert mcp_registry._SKILLS_MARKETPLACE_FETCHED_AT is None


_ENTRY = {
    "plugin": "docs-kit",
    "skill": "writing",
    "category": "documentation",
    "description": "Write docs well",
    "skill_md_url": _ANTHROPIC_RAW + "/writing/SKILL.md",
    "homepage": (
        "https://github.com/anthropics/claude-plugins-official"
        "/tree/main/plugins/docs-kit/skills/writing"
    ),
    "license": "Apache-2.0",
    "author": "Anthropic",
}


def _install_env(monkeypatch, tmp_path, body):
    async def fake_marketplace():
        return [_ENTRY]

    monkeypatch.setattr(mcp_registry, "_fetch_skills_marketplace", fake_marketplace)
    monkeypatch.setattr(mcp_registry, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(
        mcp_registry, "httpx", FakeHttpx(lambda url, params: FakeResponse(text=body))
    )


def test_install_skill_writes_attribution_and_a_default_risk_manifest(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, "name: writing\ndescription: Write docs well\n")

    result = asyncio.run(mcp_registry.install_skill("docs-kit", "writing"))

    skill_dir = tmp_path / "skills" / "writing"
    assert result == {
        "status": "installed",
        "plugin": "docs-kit",
        "skill": "writing",
        "path": str(skill_dir),
        "license": "Apache-2.0",
        "author": "Anthropic",
    }
    written = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert written.startswith("<!-- Source: " + _ENTRY["homepage"] + ", Apache-2.0 -->\n")
    assert written.endswith("description: Write docs well\n")
    assert json.loads((skill_dir / "risk.json").read_text(encoding="utf-8")) == {
        "risk": "read",
        "destructive": False,
        "shell": False,
        "network": False,
        "auto_approve": True,
        "sandbox": "workspace",
        "rollback": "none",
    }


def test_install_skill_does_not_double_attribute_or_overwrite_risk(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, "<!-- Source: upstream, Apache-2.0 -->\nbody\n")
    skill_dir = tmp_path / "skills" / "writing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "risk.json").write_text('{"risk": "custom"}', encoding="utf-8")

    asyncio.run(mcp_registry.install_skill("docs-kit", "writing"))

    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == (
        "<!-- Source: upstream, Apache-2.0 -->\nbody\n"
    )
    assert json.loads((skill_dir / "risk.json").read_text(encoding="utf-8")) == {"risk": "custom"}


def test_install_skill_rejects_a_skill_absent_from_the_marketplace(monkeypatch, tmp_path):
    _install_env(monkeypatch, tmp_path, "body")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(mcp_registry.install_skill("docs-kit", "missing"))

    assert exc.value.status_code == 404
    assert "docs-kit/missing" in exc.value.detail
    assert not (tmp_path / "skills").exists()
