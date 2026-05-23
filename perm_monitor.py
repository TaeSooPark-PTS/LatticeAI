#!/usr/bin/env python3
"""
Lattice AI — Permission Queue Helper
--------------------------------------
Claude Code가 Discord MCP 플러그인을 통해 권한 요청을 처리하는 방식으로 설계됨.
직접 실행 시: 대기 중인 권한 요청 목록 출력 및 approve/deny API 호출 보조.

사용법 (Claude Code 세션 내에서):
    python3 perm_monitor.py             # 대기 중인 권한 요청 목록 출력
    python3 perm_monitor.py approve TOKEN  # 특정 토큰 승인
    python3 perm_monitor.py deny TOKEN     # 특정 토큰 거부

읽는 ENV:
    LATTICEAI_PERMISSION_SECRET  — approve/deny API용 시크릿
    LATTICEAI_PORT               — Lattice AI 서버 포트 (기본 4825)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Load .env ─────────────────────────────────────────────────────────────────
_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

PERM_SECRET = os.environ.get("LATTICEAI_PERMISSION_SECRET", "")
SERVER_PORT = int(os.environ.get("LATTICEAI_PORT", "4825"))
SERVER_BASE = f"http://127.0.0.1:{SERVER_PORT}"
DATA_DIR = Path(os.environ.get("LATTICEAI_DATA_DIR", str(Path.home() / ".ltcai")))
QUEUE_FILE = DATA_DIR / "permission_queue.json"

_ACTION_LABELS = {
    "list": "폴더 목록 보기",
    "read": "파일 읽기",
    "write": "파일 쓰기",
}


def _server_post(path: str) -> dict:
    """POST to Lattice AI server with permission secret auth."""
    if not PERM_SECRET:
        print("ERROR: LATTICEAI_PERMISSION_SECRET not set", file=sys.stderr)
        sys.exit(1)
    req = urllib.request.Request(
        f"{SERVER_BASE}{path}",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {PERM_SECRET}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {exc.code}: {body}"}
    except Exception as exc:
        return {"error": str(exc)}


def list_pending() -> dict:
    """Read queue file and return pending (non-expired, non-approved) requests."""
    if not QUEUE_FILE.exists():
        return {}
    try:
        queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    now = time.time()
    return {
        tok: rec
        for tok, rec in queue.items()
        if float(rec.get("expires_at", 0)) > now and not rec.get("approved")
    }


def cmd_list() -> None:
    pending = list_pending()
    if not pending:
        print("대기 중인 권한 요청 없음.")
        return
    print(f"대기 중인 권한 요청: {len(pending)}건\n")
    for tok, rec in pending.items():
        expires_in = max(0, int(float(rec.get("expires_at", 0)) - time.time()))
        action = str(rec.get("action", "?"))
        print(f"  토큰: {tok}")
        print(f"  단축: {tok[:8]}")
        print(f"  경로: {rec.get('path', '?')}")
        print(f"  작업: {_ACTION_LABELS.get(action, action)}")
        print(f"  요청자: {rec.get('user_email', '?')}")
        print(f"  만료까지: {expires_in // 60}분 {expires_in % 60}초")
        print()


def cmd_approve(token_prefix: str) -> None:
    pending = list_pending()
    # Allow partial (short) token match
    matched = [t for t in pending if t.startswith(token_prefix) or t == token_prefix]
    if not matched:
        print(f"ERROR: 토큰 '{token_prefix}'에 해당하는 대기 요청 없음")
        sys.exit(1)
    if len(matched) > 1:
        print(f"ERROR: 토큰 '{token_prefix}'이 여러 항목에 매칭됨 — 더 긴 prefix 사용")
        sys.exit(1)
    token = matched[0]
    result = _server_post(f"/permissions/approve/{token}")
    if result.get("ok"):
        print(f"✅ 승인 완료: {token[:8]}... — {result.get('path')}")
    else:
        print(f"❌ 승인 실패: {result}")
        sys.exit(1)


def cmd_deny(token_prefix: str) -> None:
    pending = list_pending()
    matched = [t for t in pending if t.startswith(token_prefix) or t == token_prefix]
    if not matched:
        print(f"ERROR: 토큰 '{token_prefix}'에 해당하는 대기 요청 없음")
        sys.exit(1)
    if len(matched) > 1:
        print(f"ERROR: 토큰 '{token_prefix}'이 여러 항목에 매칭됨 — 더 긴 prefix 사용")
        sys.exit(1)
    token = matched[0]
    result = _server_post(f"/permissions/deny/{token}")
    if result.get("ok"):
        print(f"❌ 거부 완료: {token[:8]}... — {result.get('path')}")
    else:
        print(f"오류: {result}")
        sys.exit(1)


def format_discord_message() -> str:
    """Return Discord-ready notification text for all pending requests."""
    pending = list_pending()
    if not pending:
        return ""
    lines = ["🔐 **파일 접근 권한 요청 대기 중**\n"]
    for tok, rec in pending.items():
        expires_in = max(0, int(float(rec.get("expires_at", 0)) - time.time()))
        action = str(rec.get("action", "?"))
        short = tok[:8]
        lines.append(
            f"**`{short}`** — `{rec.get('path', '?')}` ({_ACTION_LABELS.get(action, action)})"
            f" | 요청자: {rec.get('user_email', '?')}"
            f" | 만료: {expires_in // 60}분 {expires_in % 60}초\n"
        )
    lines.append("\n승인: `승인 <단축토큰>`  |  거부: `거부 <단축토큰>`")
    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "list":
        cmd_list()
    elif args[0] == "approve" and len(args) == 2:
        cmd_approve(args[1])
    elif args[0] == "deny" and len(args) == 2:
        cmd_deny(args[1])
    elif args[0] == "discord-msg":
        # Print Discord-formatted message for Claude Code to relay
        msg = format_discord_message()
        print(msg if msg else "(대기 중인 요청 없음)")
    else:
        print("사용법:")
        print("  python3 perm_monitor.py                # 목록")
        print("  python3 perm_monitor.py approve TOKEN  # 승인")
        print("  python3 perm_monitor.py deny TOKEN     # 거부")
        print("  python3 perm_monitor.py discord-msg    # Discord 메시지 포맷")
        sys.exit(1)
