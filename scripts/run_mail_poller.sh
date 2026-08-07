#!/usr/bin/env bash
# Compatibility alias. All mailbox runs share the canonical entrypoint and lock.
set -euo pipefail
ROOT="/opt/data"
exec "$ROOT/scripts/orchesta-rfq-mail-poller.sh" "$@"
