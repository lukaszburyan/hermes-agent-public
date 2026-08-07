#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="/opt/data"
PYTHON="${HERMES_APP_PYTHON:-/opt/hermes-release/.venv/bin/python}"
REPORT_DIR="$ROOT/reports"
REPORT="$REPORT_DIR/hermes-release-monitor-latest.json"
PARTIAL="$REPORT_DIR/.hermes-release-monitor.$$.partial"

mkdir -p "$REPORT_DIR"
set +e
"$PYTHON" "$ROOT/execution/hermes_release_monitor.py" \
  --registry-file "$ROOT/.tmp/orchesta-rfq-unified.sqlite3" \
  --source-health "$ROOT/.tmp/orchesta-rfq-source-health.json" \
  --backup-metadata "$ROOT/backups/latest.metadata.json" \
  --scheduler-inventory "$ROOT/rfq-state/scheduler-inventory.json" \
  --release-root "$ROOT" >"$PARTIAL"
CODE=$?
set -e
chmod 600 "$PARTIAL"
mv -f "$PARTIAL" "$REPORT"
exit "$CODE"
