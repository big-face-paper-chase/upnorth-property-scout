#!/usr/bin/env bash
# Daily wrapper for the Up North Property Scout.
# Runs the scan, rebuilds the public feed, publishes data.json to GitHub Pages.
set -euo pipefail
cd "$(dirname "$0")"

LOG="data/daily_$(date +%F).log"
mkdir -p data

python3 scout.py --quiet >> "$LOG" 2>&1
SCAN_EXIT=$?

python3 publish.py --data-only >> "$LOG" 2>&1 || echo "publish failed" >> "$LOG"

# keep 30 days of logs
find data -name 'daily_*.log' -mtime +30 -delete 2>/dev/null || true
exit $SCAN_EXIT
