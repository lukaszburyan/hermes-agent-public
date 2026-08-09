#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="/opt/data"
PYTHON="${HERMES_APP_PYTHON:-/opt/hermes-release/.venv/bin/python}"
LOCK="$ROOT/.tmp/hermes-rfq-claim-reaper.lock"

mkdir -p "$ROOT/.tmp"
exec 9>"$LOCK"
flock -n 9 || exit 0
"$PYTHON" "$ROOT/execution/response_claim_reaper.py" \
  --registry-file "$ROOT/.tmp/orchesta-rfq-unified.sqlite3" \
  --apply
