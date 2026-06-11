"""T9 privacy vendoring — shipped pages must make zero CDN calls.

Local-first means local: fonts, icons, and JS libraries are vendored under
static/vendor; no shipped HTML/CSS/JS may reference external CDNs, and the
service worker precaches the v3 bundle (not the legacy one).
"""

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "static"

CDN_RE = re.compile(
    r"https?://(fonts\.googleapis\.com|fonts\.gstatic\.com|cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)"
)


def _shipped_files():
    for path in STATIC.rglob("*"):
        if path.suffix not in {".html", ".css", ".js"}:
            continue
        if (STATIC / "vendor") in path.parents:
            continue  # vendored copies may cite their origin in comments
        yield path


def test_no_cdn_references_in_shipped_static_files():
    offenders = []
    for path in _shipped_files():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if CDN_RE.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{i}")
    assert not offenders, f"CDN references in shipped files: {offenders}"


def test_vendored_assets_exist_and_are_real():
    vendor = STATIC / "vendor"
    for rel, min_bytes in [
        ("fonts/inter.css", 500),
        ("fonts/inter-latin-400-normal.woff2", 10_000),
        ("fonts/inter-latin-700-normal.woff2", 10_000),
        ("icons/tabler-icons.min.css", 100_000),
        ("icons/tabler-icons.woff2", 500_000),
        ("chart.umd.min.js", 100_000),
        ("marked.min.js", 20_000),
    ]:
        path = vendor / rel
        assert path.exists(), f"missing vendored asset: {rel}"
        assert path.stat().st_size >= min_bytes, f"vendored asset suspiciously small: {rel}"
    # woff2 magic number — the font files are real fonts, not error pages.
    for woff in (vendor / "fonts").glob("*.woff2"):
        assert woff.read_bytes()[:4] == b"wOF2", f"not a woff2 file: {woff.name}"
    assert (vendor / "icons" / "tabler-icons.woff2").read_bytes()[:4] == b"wOF2"


def test_vendored_css_references_only_local_files():
    inter = (STATIC / "vendor" / "fonts" / "inter.css").read_text(encoding="utf-8")
    tabler = (STATIC / "vendor" / "icons" / "tabler-icons.min.css").read_text(encoding="utf-8")
    assert "http" not in inter
    for url in re.findall(r'url\("?([^")]+)"?\)', tabler):
        assert not url.startswith("http"), f"external url in tabler css: {url}"
        assert (STATIC / "vendor" / "icons" / url.lstrip("./").split("?")[0]).exists()


def test_service_worker_precaches_v3_not_legacy():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "asset-manifest.json" in sw
    assert "/static/vendor/fonts/inter.css" in sw
    assert "scripts/chat.js" not in sw, "sw must not precache the legacy bundle"
    assert "ltcai-v310" not in sw, "stale cache name"
    # Every static SHELL path the sw lists must exist on disk.
    for match in re.findall(r'"(/static/vendor/[^"]+)"', sw):
        assert (REPO / match.lstrip("/")).exists(), f"sw precaches missing file: {match}"


def test_v3_shell_uses_vendored_assets():
    html = (STATIC / "v3" / "index.html").read_text(encoding="utf-8")
    assert "/static/vendor/fonts/inter.css" in html
    assert "/static/vendor/icons/tabler-icons.min.css" in html


def test_asset_manifest_hashed_files_exist():
    manifest = json.loads((STATIC / "v3" / "asset-manifest.json").read_text(encoding="utf-8"))
    paths = [manifest["entrypoints"]["app"], *manifest["entrypoints"]["styles"], *manifest["assets"].values()]
    for rel in paths:
        assert (REPO / rel.lstrip("/")).exists(), f"manifest references missing file: {rel}"
