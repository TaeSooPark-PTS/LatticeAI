#!/usr/bin/env bash
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PROJECT_DIR/.ltcai.pid"
PORT="${LATTICEAI_PORT:-4825}"

cd "$PROJECT_DIR" || exit 1

host_name="lattice-host"
if [ "$(uname -s)" = "Windows_NT" ]; then
    host_name="lattice-host.exe"
fi

resolve_host() {
    if [ -n "${LATTICEAI_HOST_BIN:-}" ] && [ -x "${LATTICEAI_HOST_BIN}" ]; then
        echo "${LATTICEAI_HOST_BIN}"
        return
    fi
    if [ -n "${LTCAI_HOST:-}" ] && [ -x "${LTCAI_HOST}" ]; then
        echo "${LTCAI_HOST}"
        return
    fi
    for candidate in \
        "$PROJECT_DIR/rust/target/release/$host_name" \
        "$PROJECT_DIR/rust/target/debug/$host_name"
    do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return
        fi
    done
    if command -v lattice-host >/dev/null 2>&1; then
        command -v lattice-host
        return
    fi
}

HOST_BIN="$(resolve_host || true)"

if [ -z "${LATTICEAI_DESKTOP_BACKEND_CMD:-}" ]; then
    for py in "$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/venv/bin/python" python3; do
        if [ -x "$py" ] || command -v "$py" >/dev/null 2>&1; then
            export LATTICEAI_DESKTOP_BACKEND_CMD="$py -m latticeai.worker_app"
            break
        fi
    done
fi

if [ -z "${HOST_BIN}" ] && command -v node >/dev/null 2>&1 && [ -f "$PROJECT_DIR/bin/ltcai.js" ]; then
    # bin/ltcai.js locates or cargo-builds lattice-host and pins the worker.
    START_CMD=(node "$PROJECT_DIR/bin/ltcai.js" --port "$PORT")
elif [ -n "${HOST_BIN}" ]; then
    START_CMD=("$HOST_BIN" --port "$PORT")
else
    echo "❌ lattice-host를 찾을 수 없습니다."
    echo "   rust/ 워크스페이스에서 cargo build -p lattice-host 를 실행하거나 node가 PATH에 있는지 확인해 주세요."
    exit 1
fi

echo "🚀 24시간 무중단 AI 서버 가동을 시작합니다..."
echo "⚠️ 참고: 맥북을 닫고 외출하시려면 'Amphetamine' 앱을 켜두시거나, 전원과 모니터를 연결해 주세요."
echo "   front door: ${START_CMD[*]}"

if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE")"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "기존 Lattice AI 서버 종료 중: PID $OLD_PID"
        kill "$OLD_PID"
        sleep 2
    fi
fi

export LATTICEAI_AUTOLOAD_MODELS="${LATTICEAI_AUTOLOAD_MODELS:-false}"
export LATTICEAI_MODEL_IDLE_UNLOAD_SECONDS="${LATTICEAI_MODEL_IDLE_UNLOAD_SECONDS:-0}"
export LATTICEAI_MAX_LOCAL_MODELS="${LATTICEAI_MAX_LOCAL_MODELS:-1}"
export LATTICEAI_PORT="$PORT"

while true; do
    echo "=========================================="
    echo "🕰 시작 시간: $(date)"
    echo "=========================================="

    # caffeinate명령어로 실행되는 동안 시스템 수면을 최대한 방지합니다.
    caffeinate -i -s "${START_CMD[@]}" &
    SERVER_PID="$!"
    echo "$SERVER_PID" > "$PID_FILE"
    wait "$SERVER_PID"
    rm -f "$PID_FILE"

    echo "⚠️ 서버가 예기치 않게 종료되었습니다. 5초 후 다시 시작합니다..."
    sleep 5
done
