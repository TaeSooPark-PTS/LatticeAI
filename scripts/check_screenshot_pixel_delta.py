#!/usr/bin/env python3
"""Compare release screenshots against a prior release and fail on no-change.

Proves the layout rebuild actually moved pixels (or height), not just copy.

Usage:
  python scripts/check_screenshot_pixel_delta.py \\
    --current output/release/v10.6.3/screenshots \\
    --baseline output/release/v10.6.2/screenshots

Environment:
  LTCAI_SCREENSHOT_CURRENT            default: output/release/v{package}/screenshots
  LTCAI_SCREENSHOT_BASELINE           explicit baseline dir (must match expected version)
  LTCAI_SCREENSHOT_BASELINE_VERSION   pin expected baseline, e.g. 10.6.2 (default: prior git tag)
  LTCAI_PIXEL_MIN_PCT                 default: 1.5  (non-LivingBrain screens)
  LTCAI_PIXEL_MIN_PCT_LIVE            default: 8.0  (01/03/04 — animation alone ~7.9%)
  LTCAI_HEIGHT_MIN_PX                 default: 24   (layout height shift counts as change)

Exit 0 all screens moved enough, 1 one or more below threshold / wrong baseline, 2 bad args.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageChops

SHOTS = [
    "01-login.png",
    "02-recommended-models.png",
    "03-install-load-progress.png",
    "04-brain-chat-home.png",
    "05-memory-graph.png",
    "06-capture.png",
    "07-model-library.png",
    "08-system.png",
    "09-automation-runs.png",
    "10-admin-console.png",
    "11-knowledge-journey.png",
    "12-review-center.png",
]

# LivingBrain animation noise can hit ~7.9% with zero layout change.
LIVING_BRAIN = {
    "01-login.png",
    "03-install-load-progress.png",
    "04-brain-chat-home.png",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _package_version(root: Path) -> str:
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    return str(package["version"])


def _default_current(root: Path) -> Path:
    env = os.environ.get("LTCAI_SCREENSHOT_CURRENT")
    if env:
        return Path(env)
    version = _package_version(root)
    return root / "output" / "release" / f"v{version}" / "screenshots"


def _version_key(name: str) -> Tuple[int, ...]:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", name)
    if not m:
        return (0, 0, 0)
    return tuple(int(p) for p in m.groups())


def _normalize_version(name: str) -> str:
    return name[1:] if name.startswith("v") else name


def _git_version_tags(root: Path) -> List[str]:
    """Return sorted semver tags like ``10.6.2`` (no leading v) from the repo."""
    try:
        result = subprocess.run(
            ["git", "tag", "-l", "v*.*.*"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    tags: List[Tuple[Tuple[int, ...], str]] = []
    for line in result.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        key = _version_key(name)
        if key == (0, 0, 0):
            continue
        tags.append((key, _normalize_version(name)))
    tags.sort(key=lambda item: item[0])
    return [version for _, version in tags]


def expected_baseline_version(root: Path, current_version: Optional[str] = None) -> str:
    """Pin the baseline to a release tag — never "any older dir".

    Resolution order:
      1. ``LTCAI_SCREENSHOT_BASELINE_VERSION`` (explicit pin)
      2. Git tag whose name matches the package version (same-name tag)
         — used when the version was not bumped after a release. Comparing
         against the prior tag (e.g. 10.6.2 while package is still 10.6.3 and
         ``v10.6.3`` is tagged) lets screens that are identical to the already
         shipped release pass, because they still differ from two-releases-ago.
         Same-name baseline forces a real delta vs the published screenshots
         (materialized via ``git show`` when the on-disk dir is missing/stale).
      3. Highest git tag strictly older than the package version
      4. Patch-1 of the package version (last resort when tags are unavailable)

    This also prevents a silent downgrade to v10.6.1 when v10.6.2 is deleted
    from ``output/release/`` — which would inflate the pixel-change rate.
    """
    env = os.environ.get("LTCAI_SCREENSHOT_BASELINE_VERSION")
    if env:
        return _normalize_version(env.strip())

    current = _normalize_version(current_version or _package_version(root))
    tags = _git_version_tags(root)
    # Same-name tag = this version already shipped. Baseline is that release,
    # not the prior one (failure mode: unbumped recapture vs two-releases-ago).
    if current in tags:
        return current

    cur_key = _version_key(current)
    older = [v for v in tags if _version_key(v) < cur_key]
    if older:
        return older[-1]

    major, minor, patch = cur_key
    if patch > 0:
        return f"{major}.{minor}.{patch - 1}"
    if minor > 0:
        return f"{major}.{minor - 1}.0"
    if major > 0:
        return f"{major - 1}.0.0"
    return current


def _baseline_dir_for_version(root: Path, version: str) -> Path:
    return root / "output" / "release" / f"v{_normalize_version(version)}" / "screenshots"


def _baseline_looks_complete(shots: Path) -> bool:
    return shots.is_dir() and (shots / "01-login.png").is_file()


def _extract_baseline_from_git(root: Path, version: str) -> Optional[Path]:
    """Materialize ``v{version}`` release screenshots from the matching git tag.

    Used when the working tree no longer has ``output/release/vX.Y.Z`` but the
    tag still carries the evidence (so deleting a local dir cannot silently
    fall back to an older on-disk release).
    """
    version = _normalize_version(version)
    tag = f"v{version}"
    probe = subprocess.run(
        ["git", "rev-parse", "--verify", f"{tag}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return None

    cache = root / "output" / ".cache" / "screenshot-baseline" / f"v{version}" / "screenshots"
    cache.mkdir(parents=True, exist_ok=True)
    extracted = 0
    for name in SHOTS:
        dest = cache / name
        if dest.is_file() and dest.stat().st_size > 0:
            extracted += 1
            continue
        git_path = f"output/release/v{version}/screenshots/{name}"
        result = subprocess.run(
            ["git", "show", f"{tag}:{git_path}"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            continue
        dest.write_bytes(result.stdout)
        extracted += 1
    if extracted < len(SHOTS):
        return None
    return cache


def resolve_baseline(
    root: Path,
    current: Path,
    *,
    explicit: Optional[Path] = None,
) -> Tuple[Optional[Path], str]:
    """Return ``(baseline_dir_or_None, expected_version)``.

    Never substitutes an older-than-expected on-disk release. Callers must fail
    when the path is None.

    When the baseline version equals the package version (same-name tag path),
    prefer screenshots materialized from the git tag over
    ``output/release/vX.Y.Z/`` — that on-disk dir is where capture writes, so
    it may already hold WIP overwrites of the published release.
    """
    expected = expected_baseline_version(root)
    print(f"pixel delta: expected baseline version = v{expected}")

    if explicit is not None:
        return explicit.resolve(), expected

    env = os.environ.get("LTCAI_SCREENSHOT_BASELINE")
    if env:
        path = Path(env).resolve()
        # If the path embeds a version segment, it must match the pin.
        parent_name = path.parent.name if path.name == "screenshots" else path.name
        if parent_name.startswith("v") and _normalize_version(parent_name) != expected:
            print(
                f"pixel delta: LTCAI_SCREENSHOT_BASELINE points at {parent_name}, "
                f"expected v{expected}",
                file=sys.stderr,
            )
            return None, expected
        return path, expected

    package = _normalize_version(_package_version(root))
    same_name_as_package = _normalize_version(expected) == package

    # Same-name baseline: always take the tagged release via git show.
    # Local output/release/v{package}/ is the capture target and may already
    # contain unreleased UI (exactly the failure mode this pin prevents).
    if same_name_as_package:
        extracted = _extract_baseline_from_git(root, expected)
        if extracted is not None and _baseline_looks_complete(extracted):
            print(
                f"pixel delta: same-name tag v{expected} — "
                f"materialized baseline from git (not on-disk output/release/)"
            )
            return extracted.resolve(), expected
        print(
            f"pixel delta: same-name tag v{expected} but git materialization failed",
            file=sys.stderr,
        )
        return None, expected

    local = _baseline_dir_for_version(root, expected)
    if _baseline_looks_complete(local):
        return local.resolve(), expected

    extracted = _extract_baseline_from_git(root, expected)
    if extracted is not None and _baseline_looks_complete(extracted):
        print(f"pixel delta: materialized baseline from git tag v{expected}")
        return extracted.resolve(), expected

    return None, expected


def _default_baseline(root: Path, current: Path) -> Optional[Path]:
    """Backward-compatible helper: path only (no version). Prefer resolve_baseline."""
    path, _version = resolve_baseline(root, current)
    return path


def _pixel_delta_pct(a: Path, b: Path) -> Tuple[float, int, int, int, int]:
    """Return (changed_pct, changed_px, total_px, height_a, height_b)."""
    with Image.open(a) as im_a, Image.open(b) as im_b:
        im_a = im_a.convert("RGB")
        im_b = im_b.convert("RGB")
        h_a, h_b = im_a.size[1], im_b.size[1]
        # Align to common box so pure vertical growth still registers.
        w = max(im_a.size[0], im_b.size[0])
        h = max(h_a, h_b)
        canvas_a = Image.new("RGB", (w, h), (0, 0, 0))
        canvas_b = Image.new("RGB", (w, h), (0, 0, 0))
        canvas_a.paste(im_a, (0, 0))
        canvas_b.paste(im_b, (0, 0))
        diff = ImageChops.difference(canvas_a, canvas_b)
        # Any channel delta > 8 counts as changed (ignore sub-pixel AA noise).
        # Count on RGB getdata: a pixel is changed if any channel exceeds the
        # threshold. (A point()+convert("L") mask would average channels and
        # under-count single-channel diffs, so we do not use it.)
        pixels = list(diff.getdata())
        changed = sum(1 for r, g, b in pixels if r > 8 or g > 8 or b > 8)
        total = w * h
        pct = (100.0 * changed / total) if total else 0.0
        return pct, changed, total, h_a, h_b


def main(argv: Optional[List[str]] = None) -> int:
    root = _repo_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, default=_default_current(root))
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument(
        "--expect-baseline-version",
        type=str,
        default=None,
        help="Override expected baseline version pin (same as LTCAI_SCREENSHOT_BASELINE_VERSION)",
    )
    parser.add_argument(
        "--min-pct",
        type=float,
        default=float(os.environ.get("LTCAI_PIXEL_MIN_PCT", "1.5")),
        help="Minimum pixel change %% for non-LivingBrain screens",
    )
    parser.add_argument(
        "--min-pct-live",
        type=float,
        default=float(os.environ.get("LTCAI_PIXEL_MIN_PCT_LIVE", "8.0")),
        help="Minimum pixel change %% for LivingBrain screens (above ~7.9%% noise)",
    )
    parser.add_argument(
        "--min-height-px",
        type=int,
        default=int(os.environ.get("LTCAI_HEIGHT_MIN_PX", "24")),
        help="Height delta (px) that counts as a layout change even if pixel %% is low",
    )
    args = parser.parse_args(argv)

    if args.expect_baseline_version:
        os.environ["LTCAI_SCREENSHOT_BASELINE_VERSION"] = _normalize_version(
            args.expect_baseline_version
        )

    current = args.current.resolve()
    baseline, expected_version = resolve_baseline(
        root, current, explicit=args.baseline
    )
    print(f"pixel delta: current  = {current}")
    print(f"pixel delta: baseline = {baseline}")
    print(f"pixel delta: baseline version pin = v{expected_version}")

    if not current.is_dir():
        print(f"pixel delta: current dir missing: {current}", file=sys.stderr)
        return 2
    if baseline is None or not baseline.is_dir():
        print(
            f"pixel delta: FAIL — expected baseline v{expected_version} is missing "
            f"(will not fall back to an older release). "
            f"Restore output/release/v{expected_version}/screenshots or the git tag.",
            file=sys.stderr,
        )
        return 1

    # Final pin check: if baseline path embeds a version, it must match.
    parent_name = baseline.parent.name if baseline.name == "screenshots" else baseline.name
    if parent_name.startswith("v") and _normalize_version(parent_name) != expected_version:
        print(
            f"pixel delta: FAIL — baseline path version {parent_name} "
            f"!= expected v{expected_version}",
            file=sys.stderr,
        )
        return 1

    print(
        f"pixel delta: thresholds static>={args.min_pct:.1f}% "
        f"live>={args.min_pct_live:.1f}% height>={args.min_height_px}px"
    )
    print(
        f"{'file':<32} {'px%':>8} {'Δh':>6} {'min%':>6} {'pass':>5}"
    )
    print("-" * 64)

    failures: List[str] = []
    results: List[Dict[str, object]] = []

    for name in SHOTS:
        cur = current / name
        base = baseline / name
        if not cur.is_file():
            failures.append(f"{name}: missing in current")
            print(f"{name:<32} {'—':>8} {'—':>6} {'—':>6} FAIL")
            continue
        if not base.is_file():
            failures.append(f"{name}: missing in baseline")
            print(f"{name:<32} {'—':>8} {'—':>6} {'—':>6} FAIL")
            continue

        pct, changed, total, h_cur, h_base = _pixel_delta_pct(cur, base)
        height_delta = abs(h_cur - h_base)
        live = name in LIVING_BRAIN
        min_pct = args.min_pct_live if live else args.min_pct
        # Height growth alone proves layout rebuild for full-page shots.
        ok = pct >= min_pct or height_delta >= args.min_height_px
        tag = "ok" if ok else "FAIL"
        if not ok:
            failures.append(
                f"{name}: pixel={pct:.2f}% (need>={min_pct:.1f}%) "
                f"height_delta={height_delta}px (need>={args.min_height_px})"
            )
        print(f"{name:<32} {pct:7.2f}% {height_delta:5d}p {min_pct:5.1f}% {tag:>5}")
        results.append(
            {
                "file": name,
                "pixel_pct": round(pct, 3),
                "height_current": h_cur,
                "height_baseline": h_base,
                "height_delta": height_delta,
                "min_pct": min_pct,
                "living_brain": live,
                "pass": ok,
            }
        )

    print("-" * 64)
    if failures:
        print(f"pixel delta: FAIL — {len(failures)}/{len(SHOTS)} screen(s) below threshold")
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1

    print(f"pixel delta: PASS — all {len(SHOTS)} screens moved enough vs baseline v{expected_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
