#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="/opt/data"
PYTHON="${HERMES_APP_PYTHON:-/opt/hermes-release/.venv/bin/python}"
REPORT_DIR="$ROOT/reports"
REPORT="$REPORT_DIR/hermes-release-monitor-latest.json"
PARTIAL="$REPORT_DIR/.hermes-release-monitor.$$.partial"
KILL_SWITCH="${HERMES_TRANSPORT_KILL_SWITCH_FILE:-$ROOT/rfq-state/TRANSPORT_KILL_SWITCH}"

mkdir -p "$REPORT_DIR"
set +e
"$PYTHON" "$ROOT/execution/hermes_release_monitor.py" \
  --registry-file "$ROOT/.tmp/orchesta-rfq-unified.sqlite3" \
  --source-health "$ROOT/.tmp/orchesta-rfq-source-health.json" \
  --backup-metadata "$ROOT/backups/latest.metadata.json" \
  --scheduler-inventory "$ROOT/rfq-state/scheduler-inventory.json" \
  --release-root "$ROOT" \
  --state-file "$ROOT/rfq-state/state.sqlite3" \
  --behavior-manifest "$ROOT/BEHAVIOR_MANIFEST.json" \
  --active-app-root "/opt/hermes-release/app" >"$PARTIAL"
CODE=$?
set -e
chmod 600 "$PARTIAL"
mv -f "$PARTIAL" "$REPORT"
if [ "$CODE" -ne 0 ]; then
  KILL_SWITCH_PARTIAL="${KILL_SWITCH}.$$.partial"
  mkdir -p "$(dirname "$KILL_SWITCH")"
  printf '{"activated_at":"%s","reason":"release_monitor_critical","report":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$REPORT" >"$KILL_SWITCH_PARTIAL"
  chmod 600 "$KILL_SWITCH_PARTIAL"
  mv -f "$KILL_SWITCH_PARTIAL" "$KILL_SWITCH"
fi
exit "$CODE"
