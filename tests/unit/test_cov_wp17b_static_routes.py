"""wp17 (second pass) — the static/UI router's file routes and host probe.

Two clusters:

* ``_probe_host_capacity`` — the blocking sampler behind ``/local/sysinfo``.
  Its three subprocesses and the MLX unified-memory read are driven through
  injected fakes (``subprocess.run`` and a fake ``mlx.core`` module), so the
  parsing, the "no MLX here" fallback and the sampling-failure path all run on
  every platform instead of only on an Apple Silicon box.
* the file-backed routes (manifest / favicon / service worker / SPA shell) and
  ``/status``, each asserted present *and* absent, plus the invite-cookie
  refusals for values a client can forge.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from latticeai.api import static_routes
from latticeai.api.static_routes import (
    INVITE_COOKIE_NAME,
    PRODUCTION_CSP,
    create_static_routes_router,
)

TOP_OUT = (
    "Processes: 512 total, 2 running, 510 sleeping\n"
    "CPU usage: 4.34% user, 10.86% sys, 84.80% idle\n"
    "SharedLibs: 500M resident\n"
)


def _vm_stat(free: int, active: int, inactive: int, wired: int, compressed: int) -> str:
    return (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                         {0}.\n"
        "Pages active:                       {1}.\n"
        "Pages inactive:                     {2}.\n"
        "Pages wired down:                   {3}.\n"
        "Pages occupied by compressor:       {4}.\n"
        "Pageins:                            999999.\n"
    ).format(free, active, inactive, wired, compressed)


BUSY_VM_STAT = _vm_stat(100, 300, 200, 400, 0)     # 900/1000 pages used → 90.0%
IDLE_VM_STAT = _vm_stat(800, 100, 50, 40, 10)      # 200/1000 pages used → 20.0%
MEMSIZE_16GB = str(16 * 1024 ** 3) + "\n"


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.returncode = 0


def _fake_run(outputs):
    def run(cmd, **_kwargs):
        return _Completed(outputs[cmd[0]])

    return run


def _install_fake_mlx(monkeypatch, *, active_bytes: int, cache_bytes: int) -> None:
    """Make the Apple-Silicon-only GPU read executable anywhere."""

    mlx = types.ModuleType("mlx")
    core = types.ModuleType("mlx.core")
    core.get_active_memory = lambda: active_bytes
    core.get_cache_memory = lambda: cache_bytes
    mlx.core = core
    monkeypatch.setitem(sys.modules, "mlx", mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", core)


def _hide_mlx(monkeypatch) -> None:
    """``None`` in sys.modules makes the import fail on every platform."""

    monkeypatch.setitem(sys.modules, "mlx", None)
    monkeypatch.setitem(sys.modules, "mlx.core", None)


def _bundle(
    static_dir: Path,
    *,
    invite_gate_enabled: bool = False,
    invite_code: str = "",
    invite_cookie_secret: str = "",
    current_model: Optional[str] = None,
    require_user=None,
):
    return create_static_routes_router(
        static_dir=static_dir,
        invite_gate_enabled=invite_gate_enabled,
        invite_code=invite_code,
        app_mode="local",
        model_router=types.SimpleNamespace(_current=current_model),
        require_user=require_user or (lambda request: "user@example.com"),
        invite_cookie_secret=invite_cookie_secret,
    )


def _client(bundle) -> TestClient:
    app = FastAPI()
    app.include_router(bundle.router)
    return TestClient(app)


def _bare_request(cookie: Optional[str] = None) -> Request:
    headers = []
    if cookie is not None:
        headers.append((b"cookie", (INVITE_COOKIE_NAME + "=" + cookie).encode("utf-8")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "query_string": b"",
        }
    )


# ── host capacity probe ────────────────────────────────────────────────────

def test_probe_host_capacity_samples_cpu_ram_and_mlx_gpu(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run({"top": TOP_OUT, "vm_stat": BUSY_VM_STAT, "sysctl": MEMSIZE_16GB}),
    )
    _install_fake_mlx(
        monkeypatch,
        active_bytes=2 * 1024 ** 3,
        cache_bytes=1024 ** 3 // 2,
    )

    result = static_routes._probe_host_capacity()

    assert result["cpu_pct"] == 15.2          # 4.34% user + 10.86% sys
    assert result["ram_pct"] == 90.0          # 900 of 1000 pages
    assert result["gpu_mem_gb"] == 2.5        # active + cache, unified memory
    assert result["gpu_mem_pct"] == 15.6      # 2.5 GB of 16 GB
    assert result["readiness"] == "low"       # RAM is the worst of the three
    assert "error" not in result


def test_probe_host_capacity_without_mlx_leaves_the_gpu_reading_at_zero(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run({"top": TOP_OUT, "vm_stat": IDLE_VM_STAT, "sysctl": MEMSIZE_16GB}),
    )
    _hide_mlx(monkeypatch)

    result = static_routes._probe_host_capacity()

    assert result["gpu_mem_gb"] == 0.0
    assert result["gpu_mem_pct"] == 0.0
    assert result["cpu_pct"] == 15.2
    assert result["ram_pct"] == 20.0
    assert result["readiness"] == "roomy"
    assert "error" not in result


def test_probe_host_capacity_reports_a_failed_sample_instead_of_raising(monkeypatch):
    def _boom(cmd, **_kwargs):
        raise OSError("top: command not found")

    monkeypatch.setattr(subprocess, "run", _boom)

    result = static_routes._probe_host_capacity()

    assert result["error"] == "top: command not found"
    assert result["cpu_pct"] == 0.0
    assert result["ram_pct"] == 0.0
    assert result["readiness"] == "roomy"


def test_sysinfo_route_returns_the_probe_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run({"top": TOP_OUT, "vm_stat": BUSY_VM_STAT, "sysctl": MEMSIZE_16GB}),
    )
    _hide_mlx(monkeypatch)

    body = _client(_bundle(tmp_path)).get("/local/sysinfo").json()

    assert body["cpu_pct"] == 15.2
    assert body["ram_pct"] == 90.0
    assert body["readiness"] == "low"


# ── invite gate ────────────────────────────────────────────────────────────

def test_invite_authorized_is_open_only_when_the_gate_is_disabled(tmp_path):
    assert _bundle(tmp_path).invite_authorized(_bare_request()) is True

    gated = _bundle(tmp_path, invite_gate_enabled=True, invite_cookie_secret="s3cret")
    assert gated.invite_authorized(_bare_request()) is False
    signed = static_routes._sign_invite_cookie("s3cret")
    assert gated.invite_authorized(_bare_request(signed)) is True


def test_unparsable_invite_cookie_is_refused_rather_than_crashing(tmp_path):
    # Neither shape survives the split/int parse; both must read as "no claim".
    assert static_routes._verify_invite_cookie("v1.not-a-number.nonce.sig", "s3cret") is False
    assert static_routes._verify_invite_cookie("v1.only-two-parts", "s3cret") is False

    gated = _client(
        _bundle(tmp_path, invite_gate_enabled=True, invite_cookie_secret="s3cret")
    )
    response = gated.get(
        "/account",
        headers={"Cookie": INVITE_COOKIE_NAME + "=v1.not-a-number.nonce.sig"},
    )

    assert response.status_code == 403
    assert "Invitation Required" in response.text


# ── file-backed routes ─────────────────────────────────────────────────────

def test_manifest_is_served_only_when_the_file_ships(tmp_path):
    missing = _client(_bundle(tmp_path))
    assert missing.get("/manifest.json").status_code == 404

    (tmp_path / "manifest.json").write_text(
        '{"name": "Lattice AI", "start_url": "/app"}', encoding="utf-8"
    )
    response = _client(_bundle(tmp_path)).get("/manifest.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    assert response.json()["name"] == "Lattice AI"


def test_favicon_prefers_the_ico_then_the_png_then_404s(tmp_path):
    assert _client(_bundle(tmp_path)).get("/favicon.ico").status_code == 404

    png_only = tmp_path / "png-only"
    (png_only / "icons").mkdir(parents=True)
    (png_only / "icons" / "favicon-32.png").write_bytes(b"\x89PNG\r\n\x1a\n png bytes")
    png_response = _client(_bundle(png_only)).get("/favicon.ico")
    assert png_response.status_code == 200
    assert png_response.headers["content-type"].startswith("image/png")
    assert png_response.content.endswith(b"png bytes")

    both = tmp_path / "both"
    (both / "icons").mkdir(parents=True)
    (both / "icons" / "favicon-32.png").write_bytes(b"png bytes")
    (both / "favicon.ico").write_bytes(b"ico bytes")
    ico_response = _client(_bundle(both)).get("/favicon.ico")
    assert ico_response.status_code == 200
    assert ico_response.headers["content-type"].startswith("image/x-icon")
    assert ico_response.content == b"ico bytes"


def test_service_worker_is_served_with_root_scope(tmp_path):
    assert _client(_bundle(tmp_path)).get("/sw.js").status_code == 404

    (tmp_path / "sw.js").write_text("self.addEventListener('install', () => {});\n", encoding="utf-8")
    response = _client(_bundle(tmp_path)).get("/sw.js")

    assert response.status_code == 200
    assert response.headers["service-worker-allowed"] == "/"
    assert response.headers["content-type"].startswith("application/javascript")
    assert "addEventListener" in response.text


def test_app_shell_is_served_with_the_production_csp(tmp_path):
    missing = _client(_bundle(tmp_path)).get("/app")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "React shell not found."

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    response = _client(_bundle(tmp_path)).get("/app")

    assert response.status_code == 200
    assert response.text == "<div id=root></div>"
    assert response.headers["content-security-policy"] == PRODUCTION_CSP
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_status_reports_the_mode_and_the_loaded_model(tmp_path):
    loaded = _client(_bundle(tmp_path, current_model="mlx-community/Qwen3-4B-4bit"))
    body = loaded.get("/status").json()

    assert body["status"] == "online"
    assert body["mode"] == "local"
    assert body["loaded_model"] == "mlx-community/Qwen3-4B-4bit"

    idle = _client(_bundle(tmp_path, current_model=None))
    assert idle.get("/status").json()["loaded_model"] == "None"
