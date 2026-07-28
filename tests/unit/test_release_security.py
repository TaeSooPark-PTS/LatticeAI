"""Regression checks for release-context and container hardening."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_machine_local_bot_and_agent_files_are_excluded_from_packages():
    package_files = set(json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["files"])
    assert {
        "!bin/pts-grok",
        "!scripts/launch-pts-grok.sh",
        "!scripts/*discord-bridge*.mjs",
        "!scripts/start-*-discord.sh",
        "!scripts/com.*.discord.plist",
        "!HEARTBEAT.md",
        "!IDENTITY.md",
        "!SOUL.md",
        "!TOOLS.md",
        "!USER.md",
    } <= package_files

    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    assert "exclude DISCORD_AGENTS.md" in manifest
    assert "exclude scripts/*discord-bridge*.mjs" in manifest
    assert "exclude scripts/start-*-discord.sh" in manifest
    assert "exclude scripts/com.*.discord.plist" in manifest
    assert "exclude bin/pts-grok" in manifest
    assert "exclude scripts/launch-pts-grok.sh" in manifest
    assert "exclude HEARTBEAT.md" in manifest
    assert "exclude IDENTITY.md" in manifest
    assert "exclude SOUL.md" in manifest
    assert "exclude TOOLS.md" in manifest
    assert "exclude USER.md" in manifest


def test_container_context_excludes_secrets_and_large_build_outputs():
    ignored = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {
        ".env",
        ".env.*",
        ".git",
        ".ltcai",
        ".ltcai-*",
        "node_modules",
        "**/node_modules",
        "src-tauri",
        "output",
        "dist",
        "agent_workspace",
        "scripts/*discord-bridge*.mjs",
        "bin/pts-grok",
        "scripts/launch-pts-grok.sh",
        "HEARTBEAT.md",
        "IDENTITY.md",
        "SOUL.md",
        "TOOLS.md",
        "USER.md",
    } <= ignored


def test_container_runs_as_non_root_with_a_healthcheck():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    install_at = dockerfile.index('RUN pip install --no-cache-dir "."')
    user_at = dockerfile.index("USER lattice")
    command_at = dockerfile.index('CMD ["python", "server.py"]')

    assert install_at < user_at < command_at
    assert "HEALTHCHECK" in dockerfile
    assert "http://127.0.0.1:4825/health" in dockerfile
    assert "LATTICEAI_AGENT_ROOT=/data/agent_workspace" in dockerfile
