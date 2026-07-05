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
        STATIC_DIR / "app" / "index.html",
    ]
    stale = []
    for path in html_files:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"/static/[^\"']+\?v=([^\"'&]+)", text):
            stale.append(f"{path.relative_to(REPO_ROOT)}:{match.group(0)}")

    assert stale == []
    manifest = STATIC_DIR / "app" / "asset-manifest.json"
    assert manifest.exists()
    text = manifest.read_text(encoding="utf-8")
    assert '"/static/app/assets/' in text
    assert re.search(r"/static/app/assets/index-[A-Za-z0-9_-]+\.js", text)
    assert not (STATIC_DIR / "v3").exists()


def test_app_shell_has_no_inline_scripts_under_strict_csp():
    source_html = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    built_html = (STATIC_DIR / "app" / "index.html").read_text(encoding="utf-8")

    for html in (source_html, built_html):
        assert not re.search(r"<script(?![^>]+\bsrc=)[^>]*>", html), "strict CSP blocks inline app-shell scripts"


def test_theme_bootstrap_is_csp_safe_and_paint_blocking():
    source_html = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    built_html = (STATIC_DIR / "app" / "index.html").read_text(encoding="utf-8")
    source_boot = REPO_ROOT / "frontend" / "public" / "theme-boot.js"
    built_boot = STATIC_DIR / "app" / "theme-boot.js"

    assert source_boot.exists()
    assert built_boot.exists()
    assert '<script src="%BASE_URL%theme-boot.js"></script>' in source_html
    assert '<script src="/static/app/theme-boot.js"></script>' in built_html
    assert not re.search(r"<script[^>]+theme-boot\.js[^>]+(?:type|defer|async)=", built_html)

    boot_text = built_boot.read_text(encoding="utf-8")
    assert "lattice.theme" in boot_text
    assert "document.documentElement.dataset.theme" in boot_text


def test_manifest_assets_are_in_python_wheel_data_files():
    manifest = json.loads((STATIC_DIR / "app" / "asset-manifest.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = pyproject["tool"]["setuptools"]["data-files"]
    packaged = {item for entries in data_files.values() for item in entries}

    missing = []
    for public_url in manifest["assets"].values():
        rel = public_url.removeprefix("/")
        if not any(fnmatch(rel, pattern) for pattern in packaged):
            missing.append(rel)

    assert missing == []


def test_legacy_pages_are_deleted_and_spa_assets_remain():
    assert not (STATIC_DIR / "lattice-reference.css").exists()
    for page in ("account.html", "activity.html", "admin.html", "agents.html", "chat.html", "graph.html", "plugins.html", "workflows.html", "workspace.html"):
        assert not (STATIC_DIR / page).exists(), f"legacy page still shipped: {page}"
    assert not (STATIC_DIR / "scripts").exists() or not list((STATIC_DIR / "scripts").glob("*.js"))
    assert not (STATIC_DIR / "css" / "reference").exists() or not list((STATIC_DIR / "css" / "reference").glob("*.css"))
    assert (STATIC_DIR / "app" / "index.html").exists()
    assert not (STATIC_DIR / "v3").exists()
    assert (STATIC_DIR / "css" / "tokens.css").exists()


def test_legacy_routes_redirect_to_app_shell():
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
    client = TestClient(app, follow_redirects=False)

    assert client.get("/").headers["location"] == "/app#/account"
    assert client.get("/account").headers["location"] == "/app#/account"
    assert client.get("/chat").headers["location"] == "/app#/chat"
    assert client.get("/admin").headers["location"] == "/app#/admin/users"


def test_vite_build_disables_production_sourcemaps():
    text = (REPO_ROOT / "vite.config.ts").read_text(encoding="utf-8")
    assert re.search(r"sourcemap:\s*false", text), "production build must set sourcemap: false"
    assert not re.search(r"sourcemap:\s*true", text), "production sourcemaps leak source and bloat payload"


def test_python_package_never_ships_sourcemaps():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    for patterns in package_data.values():
        for pattern in patterns:
            assert not pattern.endswith(".map"), f"package-data ships sourcemaps: {pattern}"

    manifest_in = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "*.map" not in manifest_in, "source distributions must not include frontend sourcemaps"

    data_files = pyproject["tool"]["setuptools"]["data-files"]
    asset_patterns = data_files["static/app/assets"]
    # A bare `*` glob silently sweeps up *.js.map sourcemaps — patterns must be extension-scoped.
    assert "static/app/assets/*" not in asset_patterns
    for pattern in asset_patterns:
        assert not pattern.endswith((".map", "/*")), f"asset pattern may include sourcemaps: {pattern}"
    # The JS/CSS the app actually loads must still be covered.
    assert "static/app/assets/*.js" in asset_patterns
    assert "static/app/assets/*.css" in asset_patterns
    assert "static/app/theme-boot.js" in data_files["static/app"]


def test_npm_package_excludes_sourcemaps_and_frontend_source():
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    files = pkg["files"]
    assert "!**/*.map" in files, "npm package must exclude sourcemaps"
    # Raw TS/JSX source under frontend/ is build input, not a runtime payload.
    assert "frontend/" not in files, "npm package must not ship frontend source"
    # The built SPA shell must still ship.
    assert "static/app/" in files


def test_lint_gate_includes_python_and_excludes_generated_output():
    pkg = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    assert "lint:python" in pkg["scripts"]
    assert "npm run lint:python" in pkg["scripts"]["lint"]

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excludes = set(pyproject["tool"]["ruff"]["extend-exclude"])
    assert "output" in excludes
    assert "outputs" in excludes

    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "output/*" in gitignore
    assert "!output/release/**" in gitignore
