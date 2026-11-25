"""Multilingual documentation and release version markers inspection/sync helper."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

CURRENT_RELEASE_BANNER_FILES: Tuple[str, ...] = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "FEATURE_STATUS.md",
    "PRIVACY.md",
    "SECURITY.md",
    "docs/COMMUNITY_AND_PLUGINS.md",
    "docs/DEVELOPMENT.md",
    "docs/ONBOARDING.md",
    "docs/TRUST_MODEL.md",
    "docs/WHY_LATTICE.md",
    "docs/kg-schema.md",
)

MARKER_FILES: Tuple[str, ...] = (
    "AGENTS.md",
    "README.md",
    "RELEASE.md",
    "docs/CHANGELOG.md",
    "RELEASE_NOTES.md",
    "ARCHITECTURE.md",
    "FEATURE_STATUS.md",
    "PRIVACY.md",
    "SECURITY.md",
    "vscode-extension/README.md",
    "docs/COMMUNITY_AND_PLUGINS.md",
    "docs/DEVELOPMENT.md",
    "docs/ONBOARDING.md",
    "docs/TRUST_MODEL.md",
    "docs/WHY_LATTICE.md",
    "docs/kg-schema.md",
)

DEFAULT_REPO = Path(__file__).resolve().parents[2]
REPO = DEFAULT_REPO

_SEMVER_CAP = r"(\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?)"


def _replace_artifacts_for_version(text: str, old_ver: str, new_ver: str) -> str:
    """Replace exact current-version artifact names while leaving historical refs intact."""
    if not old_ver or old_ver == new_ver:
        return text
    esc = re.escape(old_ver)
    updated = re.sub(rf"\bltcai-{esc}\b", f"ltcai-{new_ver}", text)
    updated = re.sub(
        rf"Lattice AI_{esc}_aarch64\.dmg",
        f"Lattice AI_{new_ver}_aarch64.dmg",
        updated,
    )
    updated = re.sub(
        rf"RELEASE_NOTES_v{esc}\.md",
        f"RELEASE_NOTES_v{new_ver}.md",
        updated,
    )
    return updated


def _update_banner_marker(
    rel: str, text: str, version: str
) -> Tuple[bool, str, str, bool]:
    pattern = rf"(Current release:\s+\*\*){_SEMVER_CAP}(\s+[—-]\s+[^*]+\*\*)"
    m = re.search(pattern, text)
    if not m:
        return False, "", text, False
    current = m.group(2)
    new_text = re.sub(
        pattern,
        lambda match: f"{match.group(1)}{version}{match.group(3)}",
        text,
        count=1,
    )
    if rel == "FEATURE_STATUS.md":
        new_text = re.sub(
            rf"(# Lattice AI Feature Status \(v){_SEMVER_CAP}(\))",
            lambda match: f"{match.group(1)}{version}{match.group(3)}",
            new_text,
            count=1,
        )
    elif rel == "ARCHITECTURE.md":
        new_text = re.sub(
            rf"(?m)^({_SEMVER_CAP})( exact artifact names:)",
            f"{version}\\3",
            new_text,
            count=1,
        )
        new_text = _replace_artifacts_for_version(new_text, current, version)
    elif rel == "SECURITY.md":
        new_text = re.sub(
            rf"((?:11|12|13)\.0\.0 through ){_SEMVER_CAP}",
            f"\\g<1>{version}",
            new_text,
            count=1,
        )
        major_minor = ".".join(version.split(".")[:2])
        new_text = re.sub(
            r"\b\d+\.\d+\.x \(latest\)",
            f"{major_minor}.x (latest)",
            new_text,
            count=1,
        )
    elif rel == "docs/DEVELOPMENT.md":
        new_text = re.sub(
            rf"(For ){_SEMVER_CAP}( release work, exact artifacts are:)",
            f"\\g<1>{version}\\g<3>",
            new_text,
            count=1,
        )
        new_text = _replace_artifacts_for_version(new_text, current, version)
    return True, current, new_text, (current != version or new_text != text)


def _update_readme_marker(text: str, version: str) -> Tuple[bool, str, str, bool]:
    pattern = rf"(The current release is \*\*){_SEMVER_CAP}(\s+[—-]\s+[^*]+\*\*)"
    m = re.search(pattern, text)
    if not m:
        # Fallback to `Current release: **X.Y.Z` if formatted with banner style
        alt = rf"(Current release:\s+\*\*){_SEMVER_CAP}(\s*[—-]?[^*]*\*\*)"
        m = re.search(alt, text)
        if not m:
            return False, "", text, False
        pattern = alt
    current = m.group(2)
    new_text = re.sub(
        pattern,
        lambda match: f"{match.group(1)}{version}{match.group(3)}",
        text,
        count=1,
    )
    if current != version:
        esc = re.escape(current)
        new_text = re.sub(
            rf"output/release/v{esc}/",
            f"output/release/v{version}/",
            new_text,
        )
        new_text = re.sub(
            rf"v{esc}-living-brain-walkthrough\.gif",
            f"v{version}-living-brain-walkthrough.gif",
            new_text,
        )
        new_text = re.sub(
            rf"!\[v{esc} Living Brain walkthrough\]",
            f"![v{version} Living Brain walkthrough]",
            new_text,
        )
        new_text = re.sub(
            rf"(Expected artifacts for ){esc}( must use exact filenames:)",
            f"\\g<1>{version}\\g<2>",
            new_text,
            count=1,
        )
        new_text = _replace_artifacts_for_version(new_text, current, version)
    return True, current, new_text, (current != version or new_text != text)


def _update_release_marker(text: str, version: str) -> Tuple[bool, str, str, bool]:
    # Match the top release heading only (`## vX.Y.Z — ...` or `## vX.Y.Z`)
    # and scope updates to the top block so historical release notes stay untouched.
    m = re.search(rf"(?m)^## v{_SEMVER_CAP}\b", text)
    if not m:
        return False, "", text, False
    current = m.group(1)
    if current == version:
        return True, current, text, False
    next_heading = re.search(r"(?m)^## v\d+\.\d+\.\d+\b", text[m.end() :])
    split_at = m.end() + next_heading.start() if next_heading else len(text)
    top_block = text[:split_at]
    rest = text[split_at:]
    esc = re.escape(current)
    top_block = re.sub(rf"(?m)^## v{esc}\b", f"## v{version}", top_block, count=1)
    top_block = _replace_artifacts_for_version(top_block, current, version)
    new_text = top_block + rest
    return True, current, new_text, new_text != text


def _update_release_notes_marker(
    text: str, version: str
) -> Tuple[bool, str, str, bool]:
    m = re.search(rf"(?m)^# \[v{_SEMVER_CAP}\s+[—-]\s+[^\]]+\]", text)
    if not m:
        return False, "", text, False
    current = m.group(1)
    if current == version:
        return True, current, text, False
    esc = re.escape(current)
    new_text = re.sub(
        rf"(?m)^(# \[v){esc}(\s+[—-]\s+[^\]]+\])",
        f"\\g<1>{version}\\g<2>",
        text,
        count=1,
    )
    new_text = re.sub(
        rf"((?:11|12|13)\.0\.0 through ){esc}",
        f"\\g<1>{version}",
        new_text,
        count=1,
    )
    new_text = re.sub(
        rf"RELEASE_NOTES_v{esc}\.md",
        f"RELEASE_NOTES_v{version}.md",
        new_text,
        count=1,
    )
    return True, current, new_text, new_text != text


def inspect_or_update_marker_file(
    rel: str,
    text: str,
    version: str,
) -> Tuple[bool, str, str, bool]:
    """Inspect and update the active release version marker in a documentation file.

    Returns ``(ok, current_version, updated_text, changed)``.
    Historical changelog/release sections are preserved unmodified.
    """
    if rel in CURRENT_RELEASE_BANNER_FILES:
        return _update_banner_marker(rel, text, version)
    if rel == "README.md":
        return _update_readme_marker(text, version)
    if rel == "RELEASE.md":
        return _update_release_marker(text, version)
    if rel == "docs/CHANGELOG.md":
        m = re.search(rf"(?m)^## \[{_SEMVER_CAP}\]", text)
        if not m:
            return False, "", text, False
        current = m.group(1)
        if current == version:
            return True, current, text, False
        new_text = re.sub(
            rf"(?m)^(## \[){re.escape(current)}(\])",
            f"\\g<1>{version}\\g<2>",
            text,
            count=1,
        )
        return True, current, new_text, new_text != text
    if rel == "RELEASE_NOTES.md":
        return _update_release_notes_marker(text, version)
    if rel == "vscode-extension/README.md":
        pattern = rf"(\*\*){_SEMVER_CAP}(\s+[—-]\s+[^*]+\*\*)"
        m = re.search(pattern, text)
        if not m:
            return False, "", text, False
        current = m.group(2)
        if current == version:
            return True, current, text, False
        new_text = re.sub(
            pattern,
            lambda match: f"{match.group(1)}{version}{match.group(3)}",
            text,
            count=1,
        )
        new_text = _replace_artifacts_for_version(new_text, current, version)
        return True, current, new_text, new_text != text
    return False, "", text, False


def bump_markers(
    version: str,
    *,
    check: bool = False,
    repo: Optional[Path] = None,
    files: Optional[Sequence[str]] = None,
) -> int:
    """Inspect or synchronize documentation & release version markers."""
    from scripts.bump_version.targets import is_valid_semver, resolve_repo

    if not is_valid_semver(version):
        print(f"error: '{version}' is not a sane semantic version", file=sys.stderr)
        return 2

    target_repo = resolve_repo(repo)
    marker_list: List[str] = list(files if files is not None else MARKER_FILES)
    failures = 0

    for rel in marker_list:
        path = target_repo / rel
        if not path.exists():
            print(f"  skip  {rel} (missing)")
            continue
        text = path.read_text(encoding="utf-8")
        ok, current, new_text, changed = inspect_or_update_marker_file(
            rel, text, version
        )
        if not ok:
            print(f"  FAIL  {rel}: release marker not found")
            failures += 1
            continue
        if check:
            status = "ok" if current == version else f"MISMATCH ({current})"
            if current != version:
                failures += 1
            print(f"  {status:>9}  {rel}")
            continue
        if changed:
            path.write_text(new_text, encoding="utf-8")
            print(f"  bumped  {rel}")
        else:
            print(f"  ok      {rel}")

    return 1 if failures else 0
