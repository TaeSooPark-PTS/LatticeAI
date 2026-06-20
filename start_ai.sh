#!/usr/bin/env bash
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PROJECT_DIR/.ltcai.pid"

cd "$PROJECT_DIR" || exit 1

if [ -x "$PROJECT_DIR/.venv/bin/uvicorn" ]; then
    UVICORN_BIN="$PROJECT_DIR/.venv/bin/uvicorn"
else
    UVICORN_BIN="$PROJECT_DIR/venv/bin/uvicorn"
fi

if [ ! -x "$UVICORN_BIN" ]; then
    echo "❌ uvicorn 실행 파일을 찾을 수 없습니다: $UVICORN_BIN"
    echo "   먼저 venv와 requirements 설치 상태를 확인해 주세요."
    exit 1
fi

echo "🚀 24시간 무중단 AI 서버 가동을 시작합니다..."
echo "⚠️ 참고: 맥북을 닫고 외출하시려면 'Amphetamine' 앱을 켜두시거나, 전원과 모니터를 연결해 주세요."

# 이 프로젝트에서 띄운 기존 서버만 종료
if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE")"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "기존 Lattice AI 서버 종료 중: PID $OLD_PID"
        kill "$OLD_PID"
        sleep 2
    fi
fi

export LATTICEAI_AUTOLOAD_MODELS="${LATTICEAI_AUTOLOAD_MODELS:-true}"
export LATTICEAI_MODEL_IDLE_UNLOAD_SECONDS="${LATTICEAI_MODEL_IDLE_UNLOAD_SECONDS:-0}"
export LATTICEAI_MAX_LOCAL_MODELS="${LATTICEAI_MAX_LOCAL_MODELS:-1}"

while true; do
    echo "=========================================="
    echo "🕰 시작 시간: $(date)"
    echo "=========================================="
    
    # caffeinate명령어로 실행되는 동안 시스템 수면을 최대한 방지합니다.
    caffeinate -i -s "$UVICORN_BIN" server:app --host "${LATTICEAI_HOST:-127.0.0.1}" --port 4825 &
    SERVER_PID="$!"
    echo "$SERVER_PID" > "$PID_FILE"
    wait "$SERVER_PID"
    rm -f "$PID_FILE"
    
    echo "⚠️ 서버가 예기치 않게 종료되었습니다. 5초 후 다시 시작합니다..."
    sleep 5
done
