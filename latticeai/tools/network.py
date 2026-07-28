"""Network status probe (no shell, short timeouts)."""

from __future__ import annotations

import re
import socket
import subprocess
from typing import Any, Dict, List

from latticeai.core.quiet import quiet


def _run_network_command(parts: List[str], timeout: int = 5) -> str:
    try:
        completed = subprocess.run(parts, capture_output=True, text=True, timeout=timeout, check=False)
        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()
    except Exception:
        return ""


def network_status() -> Dict[str, Any]:
    """현재 Mac의 내부 IP, 외부 IP, 주요 네트워크 정보를 반환합니다."""
    local_ips: Dict[str, str] = {}
    for interface in ["en0", "en1", "bridge100"]:
        value = _run_network_command(["ipconfig", "getifaddr", interface])
        if value:
            local_ips[interface] = value

    ifconfig_text = _run_network_command(["ifconfig"])
    current_interface = ""
    for line in ifconfig_text.splitlines():
        if line and not line.startswith(("\t", " ")):
            current_interface = line.split(":", 1)[0]
            continue
        match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\b", line)
        if match and current_interface and match.group(1) != "127.0.0.1":
            local_ips.setdefault(current_interface, match.group(1))

    hostname = socket.gethostname()
    guessed_ip = ""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            guessed_ip = sock.getsockname()[0]
    except Exception:
        quiet()
    if guessed_ip and guessed_ip not in local_ips.values():
        local_ips["default_route"] = guessed_ip

    public_ip = _run_network_command(["curl", "-sS", "--max-time", "3", "https://api.ipify.org"])
    wifi_info = _run_network_command(["networksetup", "-getinfo", "Wi-Fi"])

    primary_local_ip = local_ips.get("en0") or local_ips.get("en1") or guessed_ip or ""
    return {
        "hostname": hostname,
        "local_ip": primary_local_ip,
        "local_ips": local_ips,
        "public_ip": public_ip,
        "wifi_info": wifi_info,
        "ifconfig_available": bool(ifconfig_text),
        "note": "local_ip은 같은 네트워크 안에서 보이는 내부 IP이고, public_ip는 인터넷에서 보이는 외부 IP입니다.",
    }
