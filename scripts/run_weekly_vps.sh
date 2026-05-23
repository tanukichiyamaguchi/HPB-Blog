#!/usr/bin/env bash
#
# Weekly-batch helper: schedules 7 days of posts in one Salon Board login.
#
# Designed to be run manually when desired (e.g., once a week from the VPS
# web console) or as a weekly cron entry.
#
# Usage:
#   bash scripts/run_weekly_vps.sh
#
# Or via cron (every Sunday at JST 22:00):
#   0 13 * * 0 cd /opt/hpb-blog && bash scripts/run_weekly_vps.sh

set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

if [ ! -f .env ]; then
    echo "[ERROR] .env not found in $PROJECT_ROOT" >&2
    exit 1
fi

export RUN_SALON_BOARD_POST=weekly
export WEEKLY_BATCH_DAYS=${WEEKLY_BATCH_DAYS:-7}
export UPDATE_THEME_HISTORY=true

LOG_FILE="$PROJECT_ROOT/logs/weekly-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$(dirname "$LOG_FILE")"

echo "==================================================="
echo " HPB Blog Auto-Post: Weekly Batch ($WEEKLY_BATCH_DAYS days)"
echo " Log: $LOG_FILE"
echo "==================================================="

python -m src.main 2>&1 | tee "$LOG_FILE"
EXIT_CODE="${PIPESTATUS[0]}"

if [ "$EXIT_CODE" -eq 0 ]; then
    echo "[SUCCESS] $WEEKLY_BATCH_DAYS days scheduled."
else
    echo "[FAILED] Exit code $EXIT_CODE — see $LOG_FILE and screenshots/"
fi
exit "$EXIT_CODE"
