from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from scripts import bump_version as bump_pkg
from scripts.bump_version import cargo, cli, targets

ROOT = Path(__file__).resolve().parents[2]


def _canonical() -> str:
    from latticeai import __version__

    return __version__


def _load_entrypoint_wrapper():
    path = ROOT / "scripts" / "bump_version.py"
    spec = importlib.util.spec_from_file_location("bump_version", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_wrapper_reexports_all_canonical_symbols():
    wrapper = _load_entrypoint_wrapper()
    for attr in (
        "REPO",
        "RUST_CRATES",
        "RUST_LOCKFILES",
        "OPENAPI_FRAGMENTS",
        "TARGETS",
        "bump",
        "main",
    ):
        assert hasattr(wrapper, attr), (
            f"scripts/bump_version.py missing re-export {attr}"
        )
        assert hasattr(bump_pkg, attr), f"scripts.bump_version missing export {attr}"
    assert wrapper.RUST_CRATES == bump_pkg.RUST_CRATES
    assert wrapper.RUST_LOCKFILES == bump_pkg.RUST_LOCKFILES
    assert wrapper.OPENAPI_FRAGMENTS == bump_pkg.OPENAPI_FRAGMENTS
    assert wrapper.TARGETS == bump_pkg.TARGETS


def test_cli_subcommands_check_against_live_repo():
    v = _canonical()
    assert cli.main([v, "--check"]) == 0
    assert cli.main(["all", v, "--check"]) == 0
    assert cli.main(["openapi", v, "--check"]) == 0
    assert cli.main(["cargo", v, "--check"]) == 0
    assert cli.main(["markers", v, "--check"]) == 0


def test_invalid_semver_rejected_across_all_subcommands(tmp_path, monkeypatch):
    monkeypatch.setattr(targets, "REPO", tmp_path)
    assert cli.main(["not-a-semver"]) == 2
    assert cli.main(["all", "1.2"]) == 2
    assert cli.main(["openapi", "bad_version"]) == 2
    assert cli.main(["cargo", "v1.2.3"]) == 2
    assert cli.main(["markers", "invalid_marker_ver"]) == 2


def test_openapi_subcommand_writes_and_updates_sha256_index(tmp_path, monkeypatch):
    (tmp_path / "frontend").mkdir(parents=True)
    openapi_dir = tmp_path / "rust" / "fixtures" / "openapi"
    openapi_dir.mkdir(parents=True)

    (tmp_path / "frontend" / "openapi.json").write_text(
        json.dumps(
            {
                "info": {"title": "Lattice AI", "version": "1.0.0"},
                "openapi": "3.1.0",
                "paths": {},
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (openapi_dir / "_envelope.json").write_text(
        json.dumps(
            {
                "family": "_envelope",
                "spec": {
                    "info": {"title": "Lattice AI", "version": "1.0.0"},
                    "openapi": "3.1.0",
                },
                "spec_version": "1.0.0",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (openapi_dir / "admin.json").write_text(
        json.dumps(
            {"family": "admin", "paths": {}, "spec_version": "1.0.0"},
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (openapi_dir / "_index.json").write_text(
        json.dumps(
            {
                "envelope": {"file": "_envelope.json", "sha256": "stale-env"},
                "fragments": [
                    {"family": "admin", "file": "admin.json", "sha256": "stale-frag"}
                ],
                "spec_version": "1.0.0",
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(targets, "REPO", tmp_path)

    assert cli.main(["openapi", "2.1.0", "--check"]) == 1
    assert cli.main(["openapi", "2.1.0"]) == 0
    assert cli.main(["openapi", "2.1.0"]) == 0
    assert cli.main(["openapi", "2.1.0", "--check"]) == 0

    idx = json.loads((openapi_dir / "_index.json").read_text(encoding="utf-8"))
    expected_env_sha = hashlib.sha256(
        (openapi_dir / "_envelope.json").read_bytes()
    ).hexdigest()
    expected_frag_sha = hashlib.sha256(
        (openapi_dir / "admin.json").read_bytes()
    ).hexdigest()
    assert idx["spec_version"] == "2.1.0"
    assert idx["envelope"]["sha256"] == expected_env_sha
    assert idx["fragments"][0]["sha256"] == expected_frag_sha


def test_cargo_subcommand_updates_manifests_and_lockfiles(tmp_path, monkeypatch):
    (tmp_path / "rust").mkdir(parents=True)
    (tmp_path / "src-tauri").mkdir(parents=True)

    (tmp_path / "rust" / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["lattice-core", "lattice-agent"]\n\n[workspace.package]\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "src-tauri" / "Cargo.toml").write_text(
        '[package]\nname = "lattice-ai-desktop"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (tmp_path / "src-tauri" / "tauri.conf.json").write_text(
        json.dumps({"productName": "Lattice AI", "version": "1.0.0"}, indent=2) + "\n",
        encoding="utf-8",
    )

    lock_entries = "\n".join(
        f'[[package]]\nname = "{crate}"\nversion = "1.0.0"\n'
        for crate in cargo.RUST_CRATES
    )
    (tmp_path / "rust" / "Cargo.lock").write_text(lock_entries, encoding="utf-8")
    (tmp_path / "src-tauri" / "Cargo.lock").write_text(
        '[[package]]\nname = "lattice-ai-desktop"\nversion = "1.0.0"\n' + lock_entries,
        encoding="utf-8",
    )

    assert cargo.parse_workspace_members(
        (tmp_path / "rust" / "Cargo.toml").read_text(encoding="utf-8")
    ) == (
        "lattice-agent",
        "lattice-core",
    )
    assert cargo.parse_workspace_members("[package]\nname = 'none'\n") == ()

    monkeypatch.setattr(targets, "REPO", tmp_path)
    assert cli.main(["cargo", "3.0.0", "--check"]) == 1
    assert cli.main(["cargo", "3.0.0"]) == 0
    assert cli.main(["cargo", "3.0.0", "--check"]) == 0

    assert 'version = "3.0.0"' in (tmp_path / "rust" / "Cargo.toml").read_text(
        encoding="utf-8"
    )
    assert 'version = "3.0.0"' in (tmp_path / "src-tauri" / "Cargo.toml").read_text(
        encoding="utf-8"
    )
    assert (
        json.loads(
            (tmp_path / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
        )["version"]
        == "3.0.0"
    )
    for crate in cargo.RUST_CRATES:
        assert f'name = "{crate}"\nversion = "3.0.0"' in (
            tmp_path / "rust" / "Cargo.lock"
        ).read_text(encoding="utf-8")


def test_markers_subcommand_updates_current_release_and_preserves_history(
    tmp_path, monkeypatch
):
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "vscode-extension").mkdir(parents=True)

    (tmp_path / "AGENTS.md").write_text(
        "# LatticeAI Project AGENTS.md\n\nCurrent release: **1.0.0 — Deep Vector**.\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Lattice AI\n\n"
        "![v1.0.0 Living Brain walkthrough](output/release/v1.0.0/gifs/v1.0.0-living-brain-walkthrough.gif)\n\n"
        "The current release is **1.0.0 — Deep Vector**:\n"
        "See [docs/releases/RELEASE_NOTES_v1.0.0.md](docs/releases/RELEASE_NOTES_v1.0.0.md).\n"
        "Expected artifacts for 1.0.0 must use exact filenames:\n"
        "- `dist/ltcai-1.0.0-py3-none-any.whl`\n"
        "- `src-tauri/target/release/bundle/dmg/Lattice AI_1.0.0_aarch64.dmg`\n",
        encoding="utf-8",
    )
    (tmp_path / "RELEASE.md").write_text(
        "# Release Guide\n\n"
        "## v1.0.0 — Deep Vector (2026-11-11)\n\n"
        "릴리스 노트는 [docs/releases/RELEASE_NOTES_v1.0.0.md](docs/releases/RELEASE_NOTES_v1.0.0.md).\n"
        "빌드 산출물은 `dist/ltcai-1.0.0-py3-none-any.whl` 입니다.\n\n"
        "## v0.9.0 — Prior Release (2026-10-01)\n\n"
        "이전 산출물은 `dist/ltcai-0.9.0-py3-none-any.whl` 입니다.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "CHANGELOG.md").write_text(
        "# Changelog\n\n"
        "## [1.0.0] - 2026-11-11 — Deep Vector\n\n"
        "- New feature.\n\n"
        "## [0.9.0] - 2026-10-01 — Prior Release\n\n"
        "- Historical entry.\n",
        encoding="utf-8",
    )
    (tmp_path / "RELEASE_NOTES.md").write_text(
        "# [v1.0.0 - Deep Vector] (2026-11-11)\n\n"
        "See [docs/releases/RELEASE_NOTES_v1.0.0.md](docs/releases/RELEASE_NOTES_v1.0.0.md).\n"
        "This repository keeps public release history from **11.0.0 through 1.0.0**.\n",
        encoding="utf-8",
    )
    (tmp_path / "ARCHITECTURE.md").write_text(
        "# Architecture\n\nCurrent release: **1.0.0 — Deep Vector**.\n\n"
        "1.0.0 exact artifact names:\n- `dist/ltcai-1.0.0-py3-none-any.whl`\n",
        encoding="utf-8",
    )
    (tmp_path / "FEATURE_STATUS.md").write_text(
        "# Lattice AI Feature Status (v1.0.0)\n\nCurrent release: **1.0.0 — Deep Vector**.\n",
        encoding="utf-8",
    )
    (tmp_path / "SECURITY.md").write_text(
        "# Security\n\nCurrent release: **1.0.0 — Deep Vector**.\n"
        "The public Git tree keeps release history from 11.0.0 through 1.0.0.\n"
        "| 1.0.x (latest) | Supported |\n",
        encoding="utf-8",
    )
    (tmp_path / "vscode-extension" / "README.md").write_text(
        "# VS Code Extension\n\n**1.0.0 — Deep Vector** adds features (`docs/releases/RELEASE_NOTES_v1.0.0.md`).\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "DEVELOPMENT.md").write_text(
        "# Development\n\nCurrent release: **1.0.0 — Deep Vector**.\n"
        "For 1.0.0 release work, exact artifacts are:\n- `dist/ltcai-1.0.0.vsix`\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(targets, "REPO", tmp_path)
    assert cli.main(["markers", "2.4.0", "--check"]) == 1
    assert cli.main(["markers", "2.4.0"]) == 0
    assert cli.main(["markers", "2.4.0", "--check"]) == 0

    assert "Current release: **2.4.0 — Deep Vector**." in (
        tmp_path / "AGENTS.md"
    ).read_text(encoding="utf-8")
    readme_text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "The current release is **2.4.0 — Deep Vector**" in readme_text
    assert (
        "output/release/v2.4.0/gifs/v2.4.0-living-brain-walkthrough.gif" in readme_text
    )
    assert "dist/ltcai-2.4.0-py3-none-any.whl" in readme_text

    release_text = (tmp_path / "RELEASE.md").read_text(encoding="utf-8")
    assert "## v2.4.0 — Deep Vector" in release_text
    assert "dist/ltcai-2.4.0-py3-none-any.whl" in release_text
    assert "## v0.9.0 — Prior Release" in release_text
    assert "dist/ltcai-0.9.0-py3-none-any.whl" in release_text

    changelog_text = (tmp_path / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [2.4.0] - 2026-11-11 — Deep Vector" in changelog_text
    assert "## [0.9.0] - 2026-10-01 — Prior Release" in changelog_text

    security_text = (tmp_path / "SECURITY.md").read_text(encoding="utf-8")
    assert "11.0.0 through 2.4.0" in security_text
    assert "2.4.x (latest)" in security_text


def test_default_bump_handles_core_targets_and_missing_fields(tmp_path, monkeypatch):
    (tmp_path / "latticeai").mkdir(parents=True)
    (tmp_path / "latticeai" / "__init__.py").write_text(
        '__version__ = "1.0.0"\n', encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "ltcai", "version": "1.0.0"}) + "\n", encoding="utf-8"
    )
    (tmp_path / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "ltcai",
                "version": "1.0.0",
                "packages": {"": {"version": "1.0.0"}},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    wrapper = _load_entrypoint_wrapper()
    monkeypatch.setattr(wrapper, "REPO", tmp_path)

    assert wrapper.bump("1.5.0", check=True) == 1
    assert wrapper.bump("1.5.0", check=False) == 0
    assert wrapper.bump("1.5.0", check=True) == 0

    lock_data = json.loads((tmp_path / "package-lock.json").read_text(encoding="utf-8"))
    assert lock_data["version"] == "1.5.0"
    assert lock_data["packages"][""]["version"] == "1.5.0"

    (tmp_path / "package.json").write_text(
        json.dumps({"name": "ltcai"}) + "\n", encoding="utf-8"
    )
    assert wrapper.bump("1.5.0", check=False) == 1


def test_module_and_script_cli_invocations():
    v = _canonical()
    res_script = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "bump_version.py"), v, "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_script.returncode == 0, res_script.stderr

    res_mod = subprocess.run(
        [sys.executable, "-m", "scripts.bump_version", "openapi", v, "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_mod.returncode == 0, res_mod.stderr


def test_screenshot_pixel_delta_has_no_legacy_pillow_getdata_fallback():
    script_text = (ROOT / "scripts" / "check_screenshot_pixel_delta.py").read_text(
        encoding="utf-8"
    )
    assert "diff.get_flattened_data()" in script_text
    assert "diff.getdata()" not in script_text


def test_ubuntu_runner_compat_script_passes():
    res = subprocess.run(
        [
            "node",
            str(ROOT / "scripts" / "check_ubuntu_runner_compat.mjs"),
            "--diagnostic",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "Verified 8 GitHub Actions workflow(s)" in res.stdout
