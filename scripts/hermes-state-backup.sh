#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="/opt/data"
PYTHON="${HERMES_APP_PYTHON:-/opt/hermes-release/.venv/bin/python}"
BACKUP_TOOL="$ROOT/execution/hermes_state_backup.py"
BACKUP_DIR="$ROOT/backups"
RECIPIENT="${HERMES_BACKUP_AGE_RECIPIENT:?Set HERMES_BACKUP_AGE_RECIPIENT to the offline recovery public key}"
OFFSITE="${HERMES_BACKUP_OFFSITE_REMOTE:?Set HERMES_BACKUP_OFFSITE_REMOTE to an rclone directory}"
RETENTION_DAYS="${HERMES_BACKUP_RETENTION_DAYS:-30}"
RETENTION_APPROVED="${HERMES_BACKUP_RETENTION_DELETE_APPROVED:-0}"

COMMIT="$(tr -d '\r\n' <"$ROOT/HERMES_RELEASE_COMMIT")"
TAG="$(tr -d '\r\n' <"$ROOT/HERMES_RELEASE_TAG")"
DIGEST="${HERMES_RELEASE_DIGEST:?Set HERMES_RELEASE_DIGEST}"
SKILL_VERSION="$($PYTHON - "$ROOT/skills/mail-lead-pipeline/SKILL.md" <<'PY'
import hashlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY
)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT="$BACKUP_DIR/hermes-state-$STAMP.tar.gz.age"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
BACKUP_ARGS=(
  create \
  --state-root "$ROOT" \
  --output "$OUTPUT" \
  --recipient "$RECIPIENT" \
  --commit "$COMMIT" \
  --tag "$TAG" \
  --image-digest "$DIGEST" \
  --skill-version "$SKILL_VERSION" \
  --offsite-remote "$OFFSITE" \
  --require-offsite \
  --retention-days "$RETENTION_DAYS"
)
if [[ "$RETENTION_APPROVED" == "1" ]]; then
  BACKUP_ARGS+=(--retention-delete-approved)
else
  echo "Backup retention deletion is not explicitly approved; creating verified off-site backup without deletion" >&2
fi
"$PYTHON" "$BACKUP_TOOL" "${BACKUP_ARGS[@]}"

LATEST_PARTIAL="$BACKUP_DIR/.latest.metadata.json.partial"
install -m 600 "$OUTPUT.metadata.json" "$LATEST_PARTIAL"
mv -f "$LATEST_PARTIAL" "$BACKUP_DIR/latest.metadata.json"
