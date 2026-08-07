#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="/opt/data"
LOG_DIR="$ROOT/logs/google-sheets-lead-poller"
STATE_FILE="$ROOT/.tmp/google-sheets-leads-state.json"
LOCK_FILE="$ROOT/.tmp/google-sheets-lead-poller.lock"
PYTHON="${HERMES_APP_PYTHON:-/opt/hermes/.venv/bin/python}"
POLLER="$ROOT/execution/google_sheets_lead_poller.py"
NOTIFY="$ROOT/execution/orchesta_rfq_email_notify.py"
REDACTOR="$ROOT/execution/redact_operational_log.py"
RETENTION="$ROOT/execution/operational_retention.py"
RAW_RETENTION_DAYS="${HERMES_RAW_LOG_RETENTION_DAYS:-7}"
SPREADSHEET_ID="${HERMES_GOOGLE_SHEETS_SPREADSHEET_ID:?Set HERMES_GOOGLE_SHEETS_SPREADSHEET_ID for this deployment}"
SHEET_NAME="Arkusz1"
TOKEN_FILE="$ROOT/google_token.json"
ZOHO_TOKEN_FILE="$ROOT/.tmp/zoho_mail_tokens.json"
ENV_FILE="$ROOT/.env"
RELEASE_TRANSPORT_KILL_SWITCH="${HERMES_TRANSPORT_KILL_SWITCH:-1}"
RELEASE_OPERATIONAL_AUTOSEND_ENABLED="${HERMES_OPERATIONAL_AUTOSEND_ENABLED:-0}"
RELEASE_TEST_MODE="${HERMES_TEST_MODE:-0}"
RELEASE_TEST_RECIPIENT_ALLOWLIST="${HERMES_TEST_RECIPIENT_ALLOWLIST:-}"
set -a
. "$ENV_FILE"
set +a
export HERMES_TRANSPORT_KILL_SWITCH="$RELEASE_TRANSPORT_KILL_SWITCH"
export HERMES_OPERATIONAL_AUTOSEND_ENABLED="$RELEASE_OPERATIONAL_AUTOSEND_ENABLED"
export HERMES_TEST_MODE="$RELEASE_TEST_MODE"
export HERMES_TEST_RECIPIENT_ALLOWLIST="$RELEASE_TEST_RECIPIENT_ALLOWLIST"
export HERMES_RFQ_MARKER_ENABLED=0
export ORCHESTA_RFQ_ALLOW_EXTERNAL_VISION=0
export ORCHESTA_RFQ_ALLOW_SCHEMA_MODEL=0
unset OPENAI_API_KEY
# Internal email summaries cover completed actions and real lead/RFQ events
# that were blocked, failed or require manual review. Plain no-op runs stay quiet;
# HERMES_INTERNAL_EMAIL_NOTIFY=0 remains an explicit kill switch.
INTERNAL_EMAIL_NOTIFY="${HERMES_INTERNAL_EMAIL_NOTIFY:-1}"
RECIPIENT="${HERMES_INTERNAL_NOTIFY_EMAIL:-}"
if [[ "$INTERNAL_EMAIL_NOTIFY" == "1" && -z "$RECIPIENT" ]]; then
  echo "Google Sheets lead poller: HERMES_INTERNAL_NOTIFY_EMAIL is required" >&2
  exit 78
fi
NOTIFY_PENDING_DIR="$ROOT/.tmp/orchesta-rfq-notify-pending"
NOTIFY_STATE_FILE="$ROOT/.tmp/orchesta-rfq-notification-ledger.json"
HEALTH_TRACKER="$ROOT/execution/source_health.py"
HEALTH_STATE="$ROOT/.tmp/orchesta-rfq-source-health.json"
RECONCILIATION_FLAG="$ROOT/rfq-state/RESTORE_RECONCILIATION_REQUIRED"

mkdir -p "$LOG_DIR" "$(dirname "$STATE_FILE")" "$(dirname "$LOCK_FILE")" "$NOTIFY_PENDING_DIR"
chmod 700 "$NOTIFY_PENDING_DIR"

if [[ -e "$RECONCILIATION_FLAG" ]]; then
  echo "Google Sheets lead poller: restore reconciliation required; transport remains paused" >&2
  exit 75
fi

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  if ! "$PYTHON" "$HEALTH_TRACKER" --state-file "$HEALTH_STATE" --source google_sheets --status failure --detail "lock_busy"; then
    echo "Google Sheets lead poller: failed to persist lock_busy health event" >&2
    exit 1
  fi
  exit 0
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$LOG_DIR/$TS.json"
ERR="$LOG_DIR/$TS.err"
TMP_OUT="$(mktemp "${TMPDIR:-/tmp}/hermes-sheets-out.XXXXXX")"
TMP_ERR="$(mktemp "${TMPDIR:-/tmp}/hermes-sheets-err.XXXXXX")"
cleanup() {
  rm -f "$TMP_OUT" "$TMP_ERR"
}
trap cleanup EXIT

set +e
POLLER_ARGS=(
  "$POLLER"
  --spreadsheet-id "$SPREADSHEET_ID" \
  --sheet-name "$SHEET_NAME" \
  --token-file "$TOKEN_FILE" \
  --state-file "$STATE_FILE"
)
POLLER_ARGS+=(--zoho-token-file "$ZOHO_TOKEN_FILE" --env-file "$ENV_FILE")
# Customer transport is enabled inside the poller from the deployment gate and
# is re-decided from the durable message type at the transport boundary. This
# wrapper must never add the legacy global --auto-send flag.
CONTROLLED_ROW="${HERMES_CONTROLLED_SHEETS_ROW:-}"
CONTROLLED_EMAIL="${HERMES_CONTROLLED_SHEETS_EMAIL:-}"
CONTROLLED_TEST_ID="${HERMES_CONTROLLED_SHEETS_TEST_ID:-}"
CONTROLLED_STATUS="${HERMES_CONTROLLED_SHEETS_EXPECTED_STATUS:-}"
if [[ -n "$CONTROLLED_ROW$CONTROLLED_EMAIL$CONTROLLED_TEST_ID$CONTROLLED_STATUS" ]]; then
  if [[ -z "$CONTROLLED_ROW" || -z "$CONTROLLED_EMAIL" || -z "$CONTROLLED_TEST_ID" || -z "$CONTROLLED_STATUS" ]]; then
    echo "Google Sheets lead poller: incomplete controlled selection" >&2
    exit 64
  fi
  POLLER_ARGS+=(
    --controlled-row-number "$CONTROLLED_ROW"
    --controlled-email "$CONTROLLED_EMAIL"
    --controlled-test-id "$CONTROLLED_TEST_ID"
    --controlled-expected-status "$CONTROLLED_STATUS"
    --process-test-status
  )
fi
timeout --signal=TERM 240s "$PYTHON" "${POLLER_ARGS[@]}" >"$TMP_OUT" 2>"$TMP_ERR"
CODE=$?
set -e

"$PYTHON" "$REDACTOR" "$TMP_OUT" "$OUT"
"$PYTHON" "$REDACTOR" "$TMP_ERR" "$ERR"
chmod 600 "$OUT" "$ERR"

if [[ $CODE -ne 0 ]]; then
  echo "Google Sheets lead poller: BŁĄD exit=$CODE"
  if [[ -s "$ERR" ]]; then
    tail -n 40 "$ERR"
  fi
  if [[ -s "$OUT" ]]; then
    tail -n 40 "$OUT"
  fi
  if ! "$PYTHON" "$HEALTH_TRACKER" --state-file "$HEALTH_STATE" --source google_sheets --status failure --detail "poller_exit_$CODE"; then
    echo "Google Sheets lead poller: failed to persist failure heartbeat" >&2
  fi
  if ! "$PYTHON" "$RETENTION" --raw-dir "$LOG_DIR" --raw-days "$RAW_RETENTION_DAYS" >/dev/null; then
    echo "Google Sheets lead poller: retention failed after poller error" >&2
  fi
  exit $CODE
fi
if ! "$PYTHON" "$HEALTH_TRACKER" --state-file "$HEALTH_STATE" --source google_sheets --status ok; then
  echo "Google Sheets lead poller: failed to persist success heartbeat" >&2
  exit 1
fi

# Internal email summaries are opt-in. Customer sends and Sheets status updates
# continue regardless of this setting.
if [[ "$INTERNAL_EMAIL_NOTIFY" == "1" ]]; then
  # Retry failed internal notifications before handling the current summary.
  shopt -s nullglob
  for PENDING in "$NOTIFY_PENDING_DIR"/*.json; do
    if "$PYTHON" "$NOTIFY" "$PENDING" \
      --token-file "$ZOHO_TOKEN_FILE" --env-file "$ENV_FILE" \
      --recipient "$RECIPIENT" --state-file "$NOTIFY_STATE_FILE"; then
      rm -f "$PENDING"
    fi
  done
  shopt -u nullglob

  # Send an internal prose notification only when the poller actually processed rows.
  if "$PYTHON" - "$OUT" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as f:
    data=json.load(f)
raise SystemExit(0 if data.get('woken', 0) else 1)
PY
  then
    set +e
    "$PYTHON" "$NOTIFY" "$OUT" \
      --token-file "$ZOHO_TOKEN_FILE" \
      --env-file "$ENV_FILE" \
      --recipient "$RECIPIENT" \
      --state-file "$NOTIFY_STATE_FILE"
    NOTIFY_CODE=$?
    set -e
    if [[ $NOTIFY_CODE -ne 0 ]]; then
      install -m 600 "$OUT" "$NOTIFY_PENDING_DIR/sheets-$TS.json"
      echo "Google Sheets lead poller notify: BŁĄD exit=$NOTIFY_CODE"
    fi
  fi
fi

# Stay silent on empty/no-op runs.
"$PYTHON" - "$OUT" <<'PY'
import json, sys
sys.path.insert(0, '/opt/data/execution')
from orchesta_rfq_email_notify import _notifiable_briefing
with open(sys.argv[1], encoding='utf-8') as f:
    data=json.load(f)
briefings=[b for b in data.get('briefings', []) if isinstance(b, dict) and _notifiable_briefing(b)]
interesting = {k: data.get(k, 0) for k in ['responses_sent','send_failures','drafts_created'] if data.get(k, 0)}
if not interesting and not briefings:
    raise SystemExit(0)
print('Google Sheets lead poller: aktywność')
for k, v in interesting.items():
    print(f'- {k}: {v}')
for b in briefings:
    telegram_text=str(b.get('telegram_text') or '').strip()
    if telegram_text:
        print(f'- {telegram_text}')
        continue
    sender=(b.get('sender') or {}).get('email') or 'unknown'
    cls=b.get('classification') or 'unknown'
    row=(b.get('sheet') or {}).get('row') or '?'
    status=(b.get('sheet') or {}).get('status') or ''
    print(f'- row {row}: {sender}: {cls}; status={status}')
PY

if ! "$PYTHON" "$RETENTION" --raw-dir "$LOG_DIR" --raw-days "$RAW_RETENTION_DAYS" >/dev/null; then
  echo "Google Sheets lead poller: retention failed" >&2
  exit 1
fi

# Secondary file-count cap protects against unexpectedly high run frequency.
find "$LOG_DIR" -type f | sort | head -n -2000 | xargs -r rm -f
