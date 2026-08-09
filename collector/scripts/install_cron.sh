#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
COLLECTOR_DIR=$(dirname "$SCRIPT_DIR")
PROJECT_DIR=$(dirname "$COLLECTOR_DIR")
PYTHON="$PROJECT_DIR/venv/bin/python"
RUNNER="$COLLECTOR_DIR/scripts/run_collector.sh"
BEGIN_MARKER="# BEGIN 10th-toy-team4-gbis-collector"
END_MARKER="# END 10th-toy-team4-gbis-collector"
CURRENT_FILE=$(mktemp)
UPDATED_FILE=$(mktemp)

cleanup() {
    rm -f "$CURRENT_FILE" "$UPDATED_FILE"
}
trap cleanup EXIT HUP INT TERM

cd "$COLLECTOR_DIR"

if [ ! -x "$PYTHON" ]; then
    printf '가상환경 Python을 찾을 수 없습니다: %s\n' "$PYTHON" >&2
    exit 1
fi

"$PYTHON" -m gbis_collector doctor
"$PYTHON" -m gbis_collector sync-metadata

crontab -l > "$CURRENT_FILE" 2>/dev/null || true

awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
    $0 == begin { skipping = 1; next }
    $0 == end { skipping = 0; next }
    !skipping { print }
' "$CURRENT_FILE" > "$UPDATED_FILE"

{
    printf '%s\n' "$BEGIN_MARKER"
    printf '%s\n' "# 출퇴근(06-10시, 16-21시): 1분 간격"
    printf '%s\n' "* 6-10,16-21 * * * $RUNNER"
    printf '%s\n' "# 그 외 시간: 5분 간격"
    printf '%s\n' "*/5 0-5,11-15,22-23 * * * $RUNNER"
    printf '%s\n' "# 노선 정류장 메타데이터: 매일 03:10 동기화"
    printf '%s\n' "10 3 * * * $RUNNER sync-metadata"
    printf '%s\n' "$END_MARKER"
} >> "$UPDATED_FILE"

crontab "$UPDATED_FILE"
printf 'cron 등록 완료: %s\n' "$RUNNER"
