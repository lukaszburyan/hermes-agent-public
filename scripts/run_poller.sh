#!/usr/bin/env bash
# Compatibility alias. All Sheets runs share the canonical entrypoint and lock.
set -euo pipefail
ROOT="/opt/data"
exec "$ROOT/scripts/google-sheets-lead-poller.sh" "$@"
