#!/usr/bin/env python3
"""Compare release screenshots against a prior release and fail on no-change.

Proves the layout rebuild actually moved pixels (or height), not just copy.

Usage:
  python scripts/check_screenshot_pixel_delta.py \\
    --current output/release/v10.6.3/screenshots \\
    --baseline output/release/v10.6.2/screenshots

Environment:
  LTCAI_SCREENSHOT_CURRENT   default: output/release/v{package}/screenshots
  LTCAI_SCREENSHOT_BASELINE  default: newest older output/release/v*/screenshots
  LTCAI_PIXEL_MIN_PCT        default: 1.5  (non-LivingBrain screens)
  LTCAI_PIXEL_MIN_PCT_LIVE   default: 8.0  (01/03/04 — animation alone ~7.9%)
  LTCAI_HEIGHT_MIN_PX        default: 24   (layout height shift counts as change)

Exit 0 all screens moved enough, 1 one or more below threshold, 2 bad args.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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


def _default_baseline(root: Path, current: Path) -> Optional[Path]:
    env = os.environ.get("LTCAI_SCREENSHOT_BASELINE")
    if env:
        return Path(env)
    release_root = root / "output" / "release"
    if not release_root.is_dir():
        return None
    current_version = current.parent.name  # vX.Y.Z
    candidates: List[Tuple[Tuple[int, ...], Path]] = []
    for entry in release_root.iterdir():
        if not entry.is_dir() or not entry.name.startswith("v"):
            continue
        if entry.name == current_version:
            continue
        shots = entry / "screenshots"
        if shots.is_dir() and (shots / "01-login.png").is_file():
            candidates.append((_version_key(entry.name), shots))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    # Prefer the highest version still older than current when parseable.
    cur_key = _version_key(current_version)
    older = [c for c in candidates if c[0] < cur_key]
    pick = older[-1] if older else candidates[-1]
    return pick[1]


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

    current = args.current.resolve()
    baseline = (args.baseline or _default_baseline(root, current) or Path()).resolve()
    if not current.is_dir():
        print(f"pixel delta: current dir missing: {current}", file=sys.stderr)
        return 2
    if not baseline.is_dir():
        print(f"pixel delta: baseline dir missing: {baseline}", file=sys.stderr)
        return 2

    print(f"pixel delta: current  = {current}")
    print(f"pixel delta: baseline = {baseline}")
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

    print(f"pixel delta: PASS — all {len(SHOTS)} screens moved enough vs baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
