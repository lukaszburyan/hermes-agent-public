#!/usr/bin/env bash
# Deployment compatibility alias. Policy stays in the canonical entrypoint.
set -euo pipefail
ROOT="/opt/data"
exec "$ROOT/scripts/orchesta-rfq-mail-poller.sh" "$@"
