"""Pin screenshot baseline selection to a release tag — never silent downgrade.

Failure modes (review):
  1. ``rm -rf output/release/v10.6.2`` used to let the gate quietly pick
     v10.6.1, inflating the change rate and still reporting PASS.
  2. Package still at 10.6.3 while tag ``v10.6.3`` exists — prior-tag pin
     (10.6.2) lets screens identical to the already-shipped release pass,
     because they still differ from two-releases-ago. Same-name tag must
     be the baseline, materialized from git (not the on-disk capture dir).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_screenshot_pixel_delta.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_screenshot_pixel_delta", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def delta():
    return _load_module()


def test_expected_baseline_version_explicit_pin_wins(delta, monkeypatch):
    monkeypatch.setenv("LTCAI_SCREENSHOT_BASELINE_VERSION", "v10.6.2")
    assert delta.expected_baseline_version(REPO) == "10.6.2"
    monkeypatch.delenv("LTCAI_SCREENSHOT_BASELINE_VERSION", raising=False)


def test_expected_baseline_version_same_name_tag_is_baseline(delta, monkeypatch):
    """Package version with a matching git tag → that tag, not the prior one.

    Failure mode: package=10.6.3, tags include v10.6.3, pin was v10.6.2 so
    unreleased WIP that is pixel-identical to shipped 10.6.3 still PASSed.
    """
    monkeypatch.delenv("LTCAI_SCREENSHOT_BASELINE_VERSION", raising=False)
    monkeypatch.setattr(
        delta,
        "_git_version_tags",
        lambda root: ["10.6.1", "10.6.2", "10.6.3"],
    )
    monkeypatch.setattr(delta, "_package_version", lambda root: "10.6.3")
    assert delta.expected_baseline_version(REPO) == "10.6.3"


def test_expected_baseline_version_prior_tag_when_unreleased(delta, monkeypatch):
    """Bumped package with no same-name tag → immediately prior tag."""
    monkeypatch.delenv("LTCAI_SCREENSHOT_BASELINE_VERSION", raising=False)
    monkeypatch.setattr(
        delta,
        "_git_version_tags",
        lambda root: ["10.6.1", "10.6.2", "10.6.3"],
    )
    monkeypatch.setattr(delta, "_package_version", lambda root: "10.6.4")
    assert delta.expected_baseline_version(REPO) == "10.6.3"


def test_expected_baseline_version_live_package_matches_tag_rule(delta, monkeypatch):
    """Against the real repo: if package version is tagged, baseline is that tag."""
    monkeypatch.delenv("LTCAI_SCREENSHOT_BASELINE_VERSION", raising=False)
    package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    version = str(package.get("version") or "")
    tags = delta._git_version_tags(REPO)
    expected = delta.expected_baseline_version(REPO)
    if version in tags:
        assert expected == version, (
            f"package {version} is tagged — baseline must be {version}, got {expected}"
        )
    else:
        older = [v for v in tags if delta._version_key(v) < delta._version_key(version)]
        if older:
            assert expected == older[-1]


def test_resolve_baseline_same_name_prefers_git_over_local_dir(
    delta, tmp_path, monkeypatch, capsys
):
    """On-disk output/release/vX may be WIP — same-name baseline uses git show."""
    monkeypatch.delenv("LTCAI_SCREENSHOT_BASELINE_VERSION", raising=False)
    monkeypatch.delenv("LTCAI_SCREENSHOT_BASELINE", raising=False)
    monkeypatch.setattr(delta, "_package_version", lambda root: "10.6.3")
    monkeypatch.setattr(
        delta, "_git_version_tags", lambda root: ["10.6.2", "10.6.3"]
    )

    # Plant a fake local dir that would be the wrong (WIP) baseline.
    local = tmp_path / "output" / "release" / "v10.6.3" / "screenshots"
    local.mkdir(parents=True)
    (local / "01-login.png").write_bytes(b"WIP-not-the-tagged-release")
    current = tmp_path / "output" / "release" / "v10.6.3-preview" / "screenshots"
    current.mkdir(parents=True)

    git_cache = tmp_path / "git-baseline" / "screenshots"
    git_cache.mkdir(parents=True)
    (git_cache / "01-login.png").write_bytes(b"tagged-release-bytes")

    monkeypatch.setattr(
        delta, "_extract_baseline_from_git", lambda root, version: git_cache
    )

    path, expected = delta.resolve_baseline(tmp_path, current)
    assert expected == "10.6.3"
    assert path == git_cache.resolve()
    assert path != local.resolve()
    out = capsys.readouterr().out
    assert "same-name tag" in out or "materialized" in out


def test_resolve_baseline_does_not_silently_downgrade(delta, tmp_path, monkeypatch, capsys):
    """Only v10.6.1 on disk + expect 10.6.2 → None (fail), not v10.6.1."""
    monkeypatch.setenv("LTCAI_SCREENSHOT_BASELINE_VERSION", "10.6.2")
    monkeypatch.delenv("LTCAI_SCREENSHOT_BASELINE", raising=False)

    release = tmp_path / "output" / "release"
    old = release / "v10.6.1" / "screenshots"
    old.mkdir(parents=True)
    (old / "01-login.png").write_bytes(b"fake-old")
    current = release / "v10.6.3" / "screenshots"
    current.mkdir(parents=True)
    (current / "01-login.png").write_bytes(b"fake-cur")

    monkeypatch.setattr(delta, "_extract_baseline_from_git", lambda root, version: None)
    monkeypatch.setattr(delta, "_git_version_tags", lambda root: ["10.6.1", "10.6.2", "10.6.3"])
    monkeypatch.setattr(delta, "_package_version", lambda root: "10.6.3")

    path, expected = delta.resolve_baseline(tmp_path, current)
    assert expected == "10.6.2"
    assert path is None, (
        f"must not fall back to v10.6.1 when v10.6.2 is missing; got {path}"
    )
    captured = capsys.readouterr()
    assert "v10.6.2" in captured.out


def test_resolve_baseline_accepts_exact_expected_dir(delta, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LTCAI_SCREENSHOT_BASELINE_VERSION", "10.6.2")
    monkeypatch.delenv("LTCAI_SCREENSHOT_BASELINE", raising=False)
    monkeypatch.setattr(delta, "_extract_baseline_from_git", lambda root, version: None)
    # Pin forces 10.6.2; package can be anything.
    monkeypatch.setattr(delta, "_package_version", lambda root: "10.6.3")

    shots = tmp_path / "output" / "release" / "v10.6.2" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "01-login.png").write_bytes(b"png")
    current = tmp_path / "output" / "release" / "v10.6.3" / "screenshots"
    current.mkdir(parents=True)

    path, expected = delta.resolve_baseline(tmp_path, current)
    assert expected == "10.6.2"
    assert path == shots.resolve()
    assert "v10.6.2" in capsys.readouterr().out


def test_main_exits_1_when_expected_baseline_missing(delta, tmp_path, monkeypatch):
    """rm -rf output/release/v10.6.2 equivalent: exit 1, never PASS."""
    monkeypatch.setenv("LTCAI_SCREENSHOT_BASELINE_VERSION", "10.6.2")
    monkeypatch.delenv("LTCAI_SCREENSHOT_BASELINE", raising=False)
    monkeypatch.setattr(delta, "_extract_baseline_from_git", lambda root, version: None)
    monkeypatch.setattr(delta, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(delta, "_package_version", lambda root: "10.6.3")

    old = tmp_path / "output" / "release" / "v10.6.1" / "screenshots"
    old.mkdir(parents=True)
    (old / "01-login.png").write_bytes(b"x")
    current = tmp_path / "output" / "release" / "v10.6.3" / "screenshots"
    current.mkdir(parents=True)
    for name in delta.SHOTS:
        (current / name).write_bytes(b"y")

    code = delta.main(["--current", str(current)])
    assert code == 1


def test_package_json_wires_screenshot_delta_gate():
    """The pixel-delta script must be an npm script on the evidence path.

    Failure mode: 12 screenshots copied from the prior release still leave the
    release pipeline green because nothing invoked the checker.
    """
    package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    scripts = package["scripts"]
    assert "check:screenshot-delta" in scripts
    assert "check_screenshot_pixel_delta.py" in scripts["check:screenshot-delta"]
    # Must not be on lint (missing baseline → exit 2 false positive on bare checkouts).
    assert "check:screenshot-delta" not in scripts.get("lint", "")
    assert "check_screenshot_pixel_delta" not in scripts.get("lint", "")
    # Must run after evidence capture (or as part of that script chain).
    evidence = scripts.get("release:evidence", "")
    artifacts = scripts.get("release:artifacts", "")
    wired = (
        "check:screenshot-delta" in evidence
        or "check_screenshot_pixel_delta" in evidence
        or "check:screenshot-delta" in artifacts
        or "check_screenshot_pixel_delta" in artifacts
    )
    assert wired, (
        "check:screenshot-delta must be chained from release:evidence or "
        f"release:artifacts; evidence={evidence!r} artifacts={artifacts!r}"
    )


def test_capture_script_blocks_tagged_release_overwrite():
    """capture_release_evidence.mjs must refuse default dir when v{version} is tagged."""
    script = (REPO / "scripts" / "capture_release_evidence.mjs").read_text(encoding="utf-8")
    assert "assertNotOverwritingTaggedRelease" in script
    assert "refs/tags/v${version}" in script or "refs/tags/v" in script
    assert "already exists" in script
    # Guard must run before wipe.
    guard_at = script.index("assertNotOverwritingTaggedRelease()")
    wipe_at = script.index("fs.rmSync(root")
    assert guard_at < wipe_at, "tag guard must run before wiping the evidence dir"
