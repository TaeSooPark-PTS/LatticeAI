#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VSIX_FILE="${1:-}"
if [[ -z "$VSIX_FILE" ]]; then
  VSIX_FILE="$(ls -t ./*.vsix 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "$VSIX_FILE" ]]; then
  echo "No VSIX found. Build one first with: npm run package"
  exit 1
fi

install_if_exists() {
  local cmd="$1"
  local label="$2"
  if command -v "$cmd" >/dev/null 2>&1; then
    "$cmd" --install-extension "$VSIX_FILE" --force
    echo "[OK] Installed into $label"
  else
    echo "[SKIP] $label CLI not found ($cmd)"
  fi
}

install_if_exists code "VS Code"
install_if_exists cursor "Cursor"
install_if_exists antigravity "Antigravity"

echo "Done."
