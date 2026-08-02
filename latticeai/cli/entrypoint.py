"""Command line entrypoint for Lattice AI."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from latticeai.cli.runtime import (
    _apply_extra_path,
    _has_module,
    _load_env_file,
)
from latticeai.core.quiet import quiet


def _local_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            addr = str(info[4][0])
            if ":" not in addr and not addr.startswith("127."):
                if addr not in ips:
                    ips.append(addr)
    except Exception:
        quiet()
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            quiet()
    return ips


def _print_banner(host: str, port: int, tunnel_url: str | None = None) -> None:
    local_url = f"http://localhost:{port}"
    print()
    print("=" * 56)
    print("  Lattice AI is running")
    print(f"  Local:    {local_url}")
    if host == "0.0.0.0":
        for ip in _local_ips():
            print(f"  Network:  http://{ip}:{port}")
        print()
        print("  Other devices on the same Wi-Fi can open the")
        print("  Network URL above in their browser.")
        print("  On iPad/Android: browser menu → 'Add to Home Screen'")
    if tunnel_url:
        print()
        print(f"  Tunnel:   {tunnel_url}")
        print("  Anyone on the internet can access via the Tunnel URL.")
    print("=" * 56)
    print()


def doctor() -> int:
    checks = [
        ("Python 3.11+", sys.version_info >= (3, 11), sys.version.split()[0], True),
        ("FastAPI", _has_module("fastapi"), "required server dependency", True),
        ("Uvicorn", _has_module("uvicorn"), "required server dependency", True),
        ("OpenAI SDK", _has_module("openai"), "required for cloud providers", False),
        ("MLX", _has_module("mlx"), "required for Apple Silicon multimodal models", False),
        ("MLX-VLM", _has_module("mlx_vlm"), "required for Gemma-4/VLM models", False),
        ("Ollama binary", shutil.which("ollama") is not None, "optional local-server engine", False),
    ]
    data_dir = Path(os.getenv("LATTICEAI_DATA_DIR") or Path.home() / ".ltcai")
    static_dir = Path(os.getenv("LATTICEAI_STATIC_DIR") or Path(__file__).resolve().parent / "static")
    checks.extend([
        ("Data dir", data_dir.exists() or data_dir.parent.exists(), str(data_dir), True),
        ("Static UI", static_dir.exists(), str(static_dir), True),
    ])

    ok = True
    for label, passed, detail, required in checks:
        icon = "OK" if passed else ("MISS" if required else "OPTIONAL")
        print(f"[{icon}] {label}: {detail}")
        ok = ok and (passed or not required)

    cloud_keys = ["OPENAI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "TOGETHER_API_KEY"]
    configured = [key for key in cloud_keys if os.getenv(key)]
    print(f"[INFO] Cloud keys configured: {', '.join(configured) if configured else 'none'}")
    return 0 if ok else 1


# ── Cloudflare Tunnel ─────────────────────────────────────────────────────────

def _cloudflared_url() -> str:
    system = sys.platform
    machine = platform.machine().lower()
    base = "https://github.com/cloudflare/cloudflared/releases/latest/download"
    if system == "darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
        return f"{base}/cloudflared-darwin-{arch}"
    if system == "win32":
        return f"{base}/cloudflared-windows-amd64.exe"
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return f"{base}/cloudflared-linux-{arch}"


def _cloudflared_bin() -> Path:
    suffix = ".exe" if sys.platform == "win32" else ""
    return Path.home() / ".latticeai" / "bin" / f"cloudflared{suffix}"


def _ensure_cloudflared() -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    dest = _cloudflared_bin()
    if dest.exists():
        return str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = _cloudflared_url()
    print("  cloudflared not found — downloading from GitHub...")
    try:
        urllib.request.urlretrieve(url, dest)
        if sys.platform != "win32":
            dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  cloudflared installed at {dest}")
        return str(dest)
    except Exception as e:
        print(f"  cloudflared download failed: {e}")
        print("  Install manually: https://developers.cloudflare.com/cloudflared/install")
        return ""


def _send_telegram(token: str, chat_id: str, text: str) -> None:
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        quiet()


def _start_tunnel(port: int) -> str | None:

    bin_path = _ensure_cloudflared()
    if not bin_path:
        return None

    log_path = Path.home() / ".latticeai" / "tunnel.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Detached on purpose: the tunnel outlives this helper; the log file is
    # the observable surface.
    subprocess.Popen(
        [bin_path, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=open(log_path, "w"),
        stderr=subprocess.STDOUT,
    )

    # Wait for the public URL (up to 30s)
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + 30
    url: str | None = None
    while time.time() < deadline:
        time.sleep(0.5)
        try:
            text = log_path.read_text(errors="replace")
            m = pattern.search(text)
            if m:
                url = m.group(0)
                break
        except Exception:
            quiet()

    if not url:
        return None

    # Telegram notification if configured
    token = os.getenv("LATTICEAI_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("LATTICEAI_TELEGRAM_CHAT_ID", "")
    if token and chat_id:
        msg = (
            f"✅ Lattice AI 시작됨\n\n"
            f"🌐 외부 URL: {url}\n"
            f"🏠 로컬: http://localhost:{port}"
        )
        threading.Thread(target=_send_telegram, args=(token, chat_id, msg), daemon=True).start()

    return url


# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    app_dir = Path(__file__).resolve().parent
    _load_env_file(app_dir / ".env")
    _apply_extra_path()

    parser = argparse.ArgumentParser(prog="LTCAI", description="Run the Lattice AI local server.")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="Check local runtime dependencies and configuration.")
    parser.add_argument("--host", default=os.getenv("LATTICEAI_HOST") or "127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("LATTICEAI_PORT") or "4825"))
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload for local development.")
    parser.add_argument(
        "--tunnel",
        action="store_true",
        help="Open a public Cloudflare tunnel so anyone can access this server from the internet.",
    )
    args = parser.parse_args()

    if args.command == "doctor":
        raise SystemExit(doctor())

    os.chdir(app_dir)

    if not args.tunnel and os.getenv("LATTICEAI_TUNNEL", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        print(
            "  LATTICEAI_TUNNEL is ignored during default local startup; "
            "restart with --tunnel to expose this server."
        )

    # --tunnel forces 0.0.0.0 so cloudflared can reach the server
    if args.tunnel and args.host == "127.0.0.1":
        args.host = "0.0.0.0"
        os.environ.setdefault("LATTICEAI_HOST", "0.0.0.0")
        os.environ.setdefault("LATTICEAI_CORS_ALLOW_NETWORK", "true")
        os.environ.setdefault("LATTICEAI_REQUIRE_AUTH", "true")

    # Keep the app config in sync with CLI flags. ``Config.from_env`` is the
    # source of truth for /mode, /health.features, SSO defaults, and routers.
    os.environ["LATTICEAI_HOST"] = str(args.host)
    os.environ["LATTICEAI_PORT"] = str(args.port)

    tunnel_url: str | None = None
    if args.tunnel:
        print()
        print("  Starting Cloudflare tunnel...")
        tunnel_url = _start_tunnel(args.port)
        if not tunnel_url:
            print("  ⚠️  Tunnel URL not obtained — server will start without tunnel.")

    _print_banner(args.host, args.port, tunnel_url)

    # Telegram startup notification (local start, tunnel handled separately inside _start_tunnel)
    if not args.tunnel:
        _tg_enabled = os.getenv("LATTICEAI_ENABLE_TELEGRAM", "").strip().lower() in ("1", "true", "yes", "on")
        _tg_token = os.getenv("LATTICEAI_TELEGRAM_BOT_TOKEN", "")
        _tg_chat  = os.getenv("LATTICEAI_TELEGRAM_CHAT_ID", "")
        if _tg_enabled and _tg_token and _tg_chat:
            _local_msg = (
                f"✅ Lattice AI 시작됨\n\n"
                f"🏠 로컬: http://localhost:{args.port}"
            )
            threading.Thread(
                target=_send_telegram,
                args=(_tg_token, _tg_chat, _local_msg),
                daemon=True,
            ).start()

    import uvicorn

    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
