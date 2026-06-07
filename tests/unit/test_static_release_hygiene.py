import json
import re
import tomllib
from fnmatch import fnmatch
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from latticeai.api.static_routes import create_static_routes_router

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static"


def test_favicon_route_serves_existing_icon():
    bundle = create_static_routes_router(
        static_dir=STATIC_DIR,
        invite_gate_enabled=False,
        invite_code="test",
        app_mode="test",
        model_router=type("Router", (), {"_current": None})(),
        require_user=lambda request: None,
    )
    app = FastAPI()
    app.include_router(bundle.router)

    client = TestClient(app)
    response = client.get("/favicon.ico")
    head_response = client.head("/favicon.ico")

    assert response.status_code == 200
    assert head_response.status_code == 200
    assert response.headers["content-type"].split(";")[0] in {"image/x-icon", "image/png"}
    assert response.content


def test_runtime_assets_use_hashed_manifest_instead_of_query_versions():
    html_files = [
        *STATIC_DIR.glob("*.html"),
        STATIC_DIR / "v3" / "index.html",
        REPO_ROOT / "tests" / "visual" / "fixtures" / "onboarding.html",
    ]
    stale = []
    for path in html_files:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"/static/[^\"']+\?v=([^\"'&]+)", text):
            stale.append(f"{path.relative_to(REPO_ROOT)}:{match.group(0)}")

    assert stale == []
    manifest = STATIC_DIR / "v3" / "asset-manifest.json"
    assert manifest.exists()
    text = manifest.read_text(encoding="utf-8")
    assert '"static/v3/js/app.js"' in text
    assert re.search(r"/static/v3/js/app\.[0-9a-f]{8}\.js", text)
    assert "asset-manifest.json" in (STATIC_DIR / "v3" / "index.html").read_text(encoding="utf-8")
    assert not (STATIC_DIR / "v3" / "js" / "core" / "fixtures.js").exists()


def test_manifest_assets_are_in_python_wheel_data_files():
    manifest = json.loads((STATIC_DIR / "v3" / "asset-manifest.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = pyproject["tool"]["setuptools"]["data-files"]
    packaged = {item for entries in data_files.values() for item in entries}

    missing = []
    for public_url in manifest["assets"].values():
        rel = public_url.removeprefix("/")
        if not any(fnmatch(rel, pattern) for pattern in packaged):
            missing.append(rel)

    assert missing == []


def test_legacy_monolith_is_removed_and_modules_present():
    # v2.2.6 replaced static/lattice-reference.css with token-native modules.
    assert not (STATIC_DIR / "lattice-reference.css").exists()
    ref = STATIC_DIR / "css" / "reference"
    for module in ("base.css", "account.css", "admin.css", "graph.css", "chat.css"):
        assert (ref / module).exists(), f"missing token-native module: {module}"
    for page in ("account.html", "admin.html", "graph.html", "chat.html"):
        text = (STATIC_DIR / page).read_text(encoding="utf-8")
        assert "lattice-reference.css" not in text, f"{page} still links the legacy monolith"
        assert "/static/css/reference/base.css" in text, f"{page} does not load the base module"
