#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '[]\n' > "$PROJECT_DIR/chat_history.json"
: > "$PROJECT_DIR/server.log"
: > "$PROJECT_DIR/ai_server.log"

echo "Lattice AI logs deleted."
echo "- chat_history.json reset"
echo "- server.log emptied"
echo "- ai_server.log emptied"
