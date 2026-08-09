#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COLLECTOR_DIR=$(dirname "$SCRIPT_DIR")
PROJECT_DIR=$(dirname "$COLLECTOR_DIR")
PYTHON="$PROJECT_DIR/venv/bin/python"
LOG_DIR="$PROJECT_DIR/data/logs"

mkdir -p "$LOG_DIR"
cd "$COLLECTOR_DIR"

if [ ! -x "$PYTHON" ]; then
    printf '가상환경 Python을 찾을 수 없습니다: %s\n' "$PYTHON" >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    set -- collect
fi

exec "$PYTHON" -m gbis_collector "$@" >> "$LOG_DIR/collector.log" 2>&1
