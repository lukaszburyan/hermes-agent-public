# Production scheduler activation for Orchesta RFQ mailbox and Sheets pollers

Use this reference when Łukasz asks whether the RFQ system is continuously active, asks to turn it on, or asks for operational status of the mailbox-and-Sheets-to-pre-offer-to-final-draft automation.

## What "active" means

The Hermes gateway being active is not enough. The RFQ pipeline is continuously active only when all of these are true:

- exactly `hermes-rfq-mail-poller.timer` and `hermes-rfq-google-sheets-poller.timer` are enabled and active,
- their services invoke only `orchesta-rfq-mail-poller.sh` and `google-sheets-lead-poller.sh`,
- root cron, user crons, old timers and direct Python poller schedules contain no active poller command,
- each source has completed a real timer tick and has a fresh persistent heartbeat,
- `.env`, `.tmp/zoho_mail_tokens.json`, Google OAuth material and the RFQ attachment/final-offer runtimes exist,
- the release monitor reports the expected commit, tag, image digest and skill hash,
- the newest encrypted state backup is recent, checksum-verified and verified off-site,
- `rfq-state/RESTORE_RECONCILIATION_REQUIRED` does not exist,
- recent source logs are written,
- wrappers stay silent on no-op runs and emit output only on activity or errors.

## Safety gates before enabling

Before enabling real mailbox polling, run offline checks:

```bash
python3 /opt/data/execution/zoho_reply_draft.py --self-test
python3 /opt/data/execution/zoho_mail_poller.py --self-test
python3 /opt/data/skills/rfq-final-offer/scripts/rfq_final_offer.py --self-test
```

Check prerequisites without printing secrets:

```bash
test -s /opt/data/.env && echo ENV_OK || echo ENV_MISSING
test -s /opt/data/.tmp/zoho_mail_tokens.json && echo TOKEN_OK || echo TOKEN_MISSING
test -d /opt/data/rfq-runtime/.venv && echo RFQ_VENV_OK || echo RFQ_VENV_MISSING
```

A fresh explicit OK from Łukasz is required before the first production activation because the pipeline touches real sources and may send safe pre-offer messages. After approval, eligible pre-offer communication is autonomous. Every final offer must remain a Zoho draft with validated PDF and must never be sent automatically.

## Wrapper shape

The production wrapper should live at:

```text
/opt/data/scripts/orchesta-rfq-mail-poller.sh
```

Recommended behavior:

- `set -euo pipefail`,
- lock with `flock` to avoid overlapping polls,
- write source JSON/error logs under `/opt/data/logs/`,
- use private SQLite state and shared registry files with mode `0600`,
- load separate deployment gates for operational transport, draft creation and final-offer generation from the protected runtime environment,
- run the mailbox poller with `--live --extract-attachments --auto-final-offer` and the Sheets poller without the legacy `--auto-send` flag; durable message policy controls every customer action,
- preflight the explicit final-offer interpreter with `import jinja2, weasyprint, pypdf` before touching Inbox,
- include `--mac-bridge-offline --skip-final-offer-obsidian` when Mac bridge/Obsidian is not mounted,
- prevent duplicate customer transport and notifications with durable operations/ledger, not process memory,
- parse JSON and print only when interesting counters are non-zero or when an error occurs,
- redact token/secret/password-like values from error output.

## Canonical systemd timers

Install only the packaged units:

```text
hermes-rfq-mail-poller.service
hermes-rfq-mail-poller.timer
hermes-rfq-google-sheets-poller.service
hermes-rfq-google-sheets-poller.timer
hermes-rfq-state-backup.service
hermes-rfq-state-backup.timer
hermes-rfq-release-monitor.service
hermes-rfq-release-monitor.timer
```

The mailbox and Sheets services each call one wrapper and therefore one distinct wrapper lock. Do not schedule either wrapper from cron. After install/update/start, verify `systemctl is-enabled`, `systemctl is-active`, `systemctl list-timers`, the service `ExecStart`, scheduler inventory JSON and a fresh source heartbeat. An enabled timer without a fresh heartbeat is not healthy.

The release-monitor service must fail when it sees an extra scheduler, old entrypoint, wrong lock, stale heartbeat/backup, unresolved `outcome_unknown`, recipient mismatch or attempted final-offer auto-send.

## Verification after enabling

Use a two-stage production check so scheduler behavior is not confused with application behavior:

1. Stop both poller timers and confirm no poller service/process is active.
2. Verify the shared registry is the exact path used by both adapters, has mode `0600`, is owned by the scheduler user, passes `PRAGMA integrity_check`, and contains the expected expand-only migration tables.
3. Run each production wrapper once manually while its timer remains stopped. Inspect the durable source-health state and latest JSON report; process exit `0` or empty stdout alone is insufficient evidence.
4. For mailbox no-op validation, require `sent_guard_failures=0`, `send_outcome_unknown=0`, and zero unexpected wake/send/draft counters. For Sheets, require `send_failures=0` and zero unexpected wake/send/draft counters.
5. Start both timers, then explicitly start each oneshot service once or wait for the next scheduled boundary.
6. Treat `systemctl start` success only as process completion. Require `Result=success`, a newer journal timestamp and a newer source heartbeat.
7. Confirm the durable source-health timestamps also advance for both sources and inspect the new report files. This distinguishes a successful scheduler tick from stale status left by an older run.
8. If a live run sends a pre-offer message, verify its Zoho Sent ID and durable response artifact. If it creates a final offer, verify the Zoho draft ID, remotely download/check the PDF, and confirm no matching final offer exists in Sent.
9. Rerun once for idempotency: no duplicate send, draft, attachment or notification.

## Pitfalls

- Do not equate a running Hermes gateway with active source pollers.
- Do not keep cron compatibility jobs alongside the systemd timers.
- Do not enable production customer transport before Łukasz approves the split policy.
- Never allow pricing, an offer, PDF or attachment through pre-offer send.
- Never expose a send operation from the final-offer branch.
- Do not trust `enabled`/`active` without a fresh heartbeat and canonical scheduler inventory.
- Do not use read/unread mailbox state as idempotency; use SQLite operations and external IDs.
- Do not recreate a customer artifact when only an internal notification retry failed.
