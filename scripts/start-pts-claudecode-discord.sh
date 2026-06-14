#!/bin/zsh
set -euo pipefail

export DISCORD_STATE_DIR="$HOME/.claude/channels/discord"
PROJECT_DIR="$HOME/Downloads/Lattice AI"
INSTALLED_BRIDGE_SCRIPT="$HOME/.claude/bin/pts-claudecode-discord-bridge.mjs"
if [[ -z "${PTS_CLAUDECODE_BRIDGE_SCRIPT:-}" && -f "$INSTALLED_BRIDGE_SCRIPT" ]]; then
  BRIDGE_SCRIPT="$INSTALLED_BRIDGE_SCRIPT"
else
  BRIDGE_SCRIPT="${PTS_CLAUDECODE_BRIDGE_SCRIPT:-$PROJECT_DIR/scripts/pts-claudecode-discord-bridge.mjs}"
fi
export PTS_CLAUDECODE_BRIDGE_SCRIPT="$BRIDGE_SCRIPT"
LOG_DIR="$HOME/.claude/logs"
LOG_FILE="$LOG_DIR/pts_claudecode_discord_autostart.log"
LOCK_DIR="$HOME/.claude/pts_claudecode_start.lock"

mkdir -p "$LOG_DIR"
chmod 600 "$DISCORD_STATE_DIR/.env" 2>/dev/null || true

stamp() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "$(stamp) another pts_claudecode start is already in progress" >> "$LOG_FILE"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

token_line=$(grep '^DISCORD_BOT_TOKEN=' "$DISCORD_STATE_DIR/.env" 2>/dev/null | tail -1 || true)
token_value="${token_line#DISCORD_BOT_TOKEN=}"

if [[ -z "$token_value" || ${#token_value} -lt 40 ]]; then
  echo "$(stamp) missing or invalid DISCORD_BOT_TOKEN in $DISCORD_STATE_DIR/.env" >> "$LOG_FILE"
  exit 1
fi

if pgrep -af '[n]ode .*pts-claudecode-discord-bridge\.mjs' >/dev/null 2>&1; then
  echo "$(stamp) pts_claudecode bridge process already running" >> "$LOG_FILE"
  exit 0
fi

cd "$PROJECT_DIR"

screen -dmS pts_claudecode_bridge zsh -lc '
  cd "$HOME/Downloads/Lattice AI"
  export DISCORD_STATE_DIR="$HOME/.claude/channels/discord"
  exec /opt/homebrew/bin/node "$PTS_CLAUDECODE_BRIDGE_SCRIPT"
'

echo "$(stamp) started pts_claudecode bridge screen" >> "$LOG_FILE"
