#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="/opt/data"
LOG_DIR="$ROOT/logs/orchesta-rfq-mail-poller"
STATE_FILE="$ROOT/rfq-state/state.sqlite3"
LOCK_FILE="$ROOT/.tmp/hermes-rfq-mail-poller.lock"
PYTHON="${HERMES_APP_PYTHON:-/opt/hermes/.venv/bin/python}"
POLLER="$ROOT/execution/zoho_mail_poller.py"
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
RFQ_PYTHON="${HERMES_RFQ_VENV_PYTHON:-$ROOT/rfq-runtime/.venv/bin/python}"
HEALTH_TRACKER="$ROOT/execution/source_health.py"
HEALTH_STATE="$ROOT/.tmp/orchesta-rfq-source-health.json"
RECONCILIATION_FLAG="$ROOT/rfq-state/RESTORE_RECONCILIATION_REQUIRED"
TOKEN_FILE="$ROOT/.tmp/zoho_mail_tokens.json"
NOTIFY="$ROOT/execution/orchesta_rfq_email_notify.py"
NOTIFY_PENDING_DIR="$ROOT/.tmp/orchesta-rfq-notify-pending"
NOTIFY_STATE_FILE="$ROOT/.tmp/orchesta-rfq-notification-ledger.json"
REDACTOR="$ROOT/execution/redact_operational_log.py"
RETENTION="$ROOT/execution/operational_retention.py"
RAW_RETENTION_DAYS="${HERMES_RAW_LOG_RETENTION_DAYS:-7}"
AUDIT_RETENTION_DAYS="${HERMES_AUDIT_RETENTION_DAYS:-90}"
# Internal email summaries are enabled for completed actions and for real
# lead/RFQ events that were blocked, deferred or require manual review.
# HERMES_INTERNAL_EMAIL_NOTIFY=0 remains an explicit kill switch.
INTERNAL_EMAIL_NOTIFY="${HERMES_INTERNAL_EMAIL_NOTIFY:-1}"
RECIPIENT="${HERMES_INTERNAL_NOTIFY_EMAIL:-}"
TARGET_EMAIL="${ZOHO_MAIL_ACCOUNT_EMAIL:?Set ZOHO_MAIL_ACCOUNT_EMAIL for this deployment}"
if [[ "$INTERNAL_EMAIL_NOTIFY" == "1" && -z "$RECIPIENT" ]]; then
  echo "Orchesta RFQ mailbox poller: HERMES_INTERNAL_NOTIFY_EMAIL is required" >&2
  exit 78
fi
# Split safety invariant: safe operational messages may be sent; final offers stay draft-only.
mkdir -p "$LOG_DIR" "$(dirname "$STATE_FILE")" "$(dirname "$LOCK_FILE")" "$NOTIFY_PENDING_DIR"
chmod 700 "$NOTIFY_PENDING_DIR"

if [[ -e "$RECONCILIATION_FLAG" ]]; then
  echo "Orchesta RFQ mailbox poller: restore reconciliation required; transport remains paused" >&2
  exit 75
fi

# Hard gate for draft-only writes. zoho_reply_draft.py still enforces mode=draft
# and the approval phrase internally; this flag only allows draft creation, never sending.
export HERMES_ALLOW_DRAFT_CREATE=1

# Prevent overlapping mailbox polls.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  if ! "$PYTHON" "$HEALTH_TRACKER" --state-file "$HEALTH_STATE" --source mailbox --status failure --detail "lock_busy"; then
    echo "Orchesta RFQ mailbox poller: failed to persist lock_busy health event" >&2
    exit 1
  fi
  exit 0
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$LOG_DIR/$TS.json"
ERR="$LOG_DIR/$TS.err"
TMP_OUT="$(mktemp "${TMPDIR:-/tmp}/hermes-mail-out.XXXXXX")"
TMP_ERR="$(mktemp "${TMPDIR:-/tmp}/hermes-mail-err.XXXXXX")"
cleanup() {
  rm -f "$TMP_OUT" "$TMP_ERR"
}
trap cleanup EXIT

set +e
POLLER_ARGS=(
  "$POLLER" \
  --live \
  --env-file "$ENV_FILE" \
  --token-file "$TOKEN_FILE" \
  --state-file "$STATE_FILE" \
  --target-email "$TARGET_EMAIL" \
  --folder "Inbox" \
  --limit 50 \
  --extract-attachments \
  --mac-bridge-offline \
  --skip-final-offer-obsidian
)
CONTROLLED_SOURCE_MESSAGE_ID="${HERMES_CONTROLLED_SOURCE_MESSAGE_ID:-}"
CONTROLLED_SENDER="${HERMES_CONTROLLED_SENDER:-}"
if [[ -n "$CONTROLLED_SOURCE_MESSAGE_ID" || -n "$CONTROLLED_SENDER" ]]; then
  if [[ -z "$CONTROLLED_SOURCE_MESSAGE_ID" || -z "$CONTROLLED_SENDER" ]]; then
    echo "Orchesta RFQ mailbox poller: BŁĄD kontrolowany test wymaga message_id i nadawcy"
    exit 2
  fi
  POLLER_ARGS+=(
    --controlled-source-message-id "$CONTROLLED_SOURCE_MESSAGE_ID"
    --controlled-sender "$CONTROLLED_SENDER"
  )
  if [[ "${HERMES_CONTROLLED_SEND_TELEGRAM:-0}" == "1" ]]; then
    POLLER_ARGS+=(--send-telegram)
  fi
elif [[ "${HERMES_CONTROLLED_SEND_TELEGRAM:-0}" == "1" ]]; then
  echo "Orchesta RFQ mailbox poller: BŁĄD kontrolowany Telegram wymaga przypiętej wiadomości testowej"
  exit 2
fi
# Customer transport is enabled inside the poller from the deployment gate and
# is re-decided from the durable message type at the transport boundary. This
# wrapper must never add the legacy global --auto-send flag.
if [[ "${HERMES_ALLOW_DRAFT_CREATE:-0}" == "1" \
  && "${HERMES_DRAFTS_ENABLED:-0}" == "1" \
  && "${HERMES_OFFER_GENERATION_ENABLED:-0}" == "1" ]]; then
  if [[ ! -x "$RFQ_PYTHON" ]]; then
    echo "Orchesta RFQ mailbox poller: BŁĄD brak izolowanego interpretera $RFQ_PYTHON"
    exit 1
  fi
  "$RFQ_PYTHON" "$ROOT/execution/rfq_runtime_preflight.py" \
    --tenant orchesta --work-dir "$ROOT/.tmp/orchesta-rfq-work"
  POLLER_ARGS+=(--auto-final-offer --final-offer-python "$RFQ_PYTHON")
fi
timeout --signal=TERM 110s "$PYTHON" "${POLLER_ARGS[@]}" >"$TMP_OUT" 2>"$TMP_ERR"
CODE=$?
set -e

# Persist only credential-redacted output. The unredacted files remain in /tmp
# for the duration of this process and are removed by the EXIT trap.
"$PYTHON" "$REDACTOR" "$TMP_OUT" "$OUT"
"$PYTHON" "$REDACTOR" "$TMP_ERR" "$ERR"
chmod 600 "$OUT" "$ERR"

if [[ $CODE -ne 0 ]]; then
  echo "Orchesta RFQ mailbox poller: BŁĄD exit=$CODE"
  if [[ -s "$ERR" ]]; then
    tail -n 40 "$ERR"
  fi
  if [[ -s "$OUT" ]]; then
    tail -n 40 "$OUT"
  fi
  if ! "$PYTHON" "$HEALTH_TRACKER" --state-file "$HEALTH_STATE" --source mailbox --status failure --detail "poller_exit_$CODE"; then
    echo "Orchesta RFQ mailbox poller: failed to persist failure heartbeat" >&2
  fi
  if ! "$PYTHON" "$RETENTION" --raw-dir "$LOG_DIR" --audit-sqlite "$STATE_FILE" \
    --raw-days "$RAW_RETENTION_DAYS" --audit-days "$AUDIT_RETENTION_DAYS" >/dev/null; then
    echo "Orchesta RFQ mailbox poller: retention failed after poller error" >&2
  fi
  exit $CODE
fi
if ! "$PYTHON" "$HEALTH_TRACKER" --state-file "$HEALTH_STATE" --source mailbox --status ok; then
  echo "Orchesta RFQ mailbox poller: failed to persist success heartbeat" >&2
  exit 1
fi

# Retry internal notification summaries independently from customer artifacts.
if [[ "$INTERNAL_EMAIL_NOTIFY" == "1" ]] && [[ -f "$NOTIFY" ]]; then
  shopt -s nullglob
  for PENDING in "$NOTIFY_PENDING_DIR"/*.json; do
    if "$PYTHON" "$NOTIFY" "$PENDING" \
      --token-file "$TOKEN_FILE" --env-file "$ENV_FILE" \
      --recipient "$RECIPIENT" --state-file "$NOTIFY_STATE_FILE"; then
      rm -f "$PENDING"
    fi
  done
  shopt -u nullglob

  # Send the current internal prose notification. Final customer offers remain draft-only.
  set +e
  "$PYTHON" "$NOTIFY" "$OUT" \
    --token-file "$TOKEN_FILE" \
    --env-file "$ENV_FILE" \
    --recipient "$RECIPIENT" \
    --state-file "$NOTIFY_STATE_FILE"
  NOTIFY_CODE=$?
  set -e
  if [[ $NOTIFY_CODE -ne 0 ]]; then
    install -m 600 "$OUT" "$NOTIFY_PENDING_DIR/mailbox-$TS.json"
    echo "Orchesta RFQ email notify: BŁĄD exit=$NOTIFY_CODE"
  fi
fi

# Stay silent on empty/no-op runs so cron does not spam Telegram.
"$PYTHON" - "$OUT" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, '/opt/data/execution')
from orchesta_rfq_email_notify import _notifiable_briefing
p = Path(sys.argv[1])
try:
    data = json.loads(p.read_text())
except Exception as e:
    print(f"Orchesta RFQ mailbox poller: nie umiem odczytać JSON: {e}")
    print(p.read_text(errors='replace')[-2000:])
    raise SystemExit(0)
interesting_keys = [
    "content_fetch_deferred", "drafts_created", "responses_sent", "send_reconciled", "send_reconcile_pending",
    "send_reconcile_failed", "would_create", "draft_duplicates_blocked",
    "attachments_extracted", "draft_notify_only", "llm_draft_created",
    "llm_draft_failed", "final_offer_attempted", "final_offer_drafts_created",
    "final_offer_pdf_created", "final_offer_missing_data", "final_offer_blocked",
    "final_offer_failed", "final_offer_notify_only", "final_offer_notifications_ready", "telegram_sent",
]
briefings = [b for b in data.get("briefings", []) if isinstance(b, dict) and _notifiable_briefing(b)]
interesting = {k: data.get(k, 0) for k in interesting_keys if data.get(k, 0)}
if not interesting and not briefings:
    raise SystemExit(0)
print("Orchesta RFQ mailbox poller: aktywność")
for k, v in interesting.items():
    print(f"- {k}: {v}")
for b in briefings:
    telegram_text = str(b.get("telegram_text") or "").strip()
    if telegram_text:
        print(f"- {telegram_text}")
        continue
    sender = (b.get("sender") or {}).get("email") or b.get("from") or "unknown"
    cls = b.get("classification") or b.get("class") or "unknown"
    draft = (b.get("draft") or {}).get("action") or "n/a"
    nxt = b.get("next_step") or ""
    print(f"- {sender}: {cls}; draft={draft}; {nxt}".strip())
PY

if ! "$PYTHON" "$RETENTION" --raw-dir "$LOG_DIR" --audit-sqlite "$STATE_FILE" \
  --raw-days "$RAW_RETENTION_DAYS" --audit-days "$AUDIT_RETENTION_DAYS" >/dev/null; then
  echo "Orchesta RFQ mailbox poller: retention failed" >&2
  exit 1
fi

# Secondary file-count cap protects against unexpectedly high run frequency.
find "$LOG_DIR" -type f | sort | head -n -2000 | xargs -r rm -f
