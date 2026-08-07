# Rollback runbook

## Preconditions

- Pollers are stopped and no RFQ process is active.
- The pre-deploy commit, tag and image digest are recorded.
- The pre-deploy encrypted state backup and checksum have been verified.
- Database rollback implications have been reviewed; no destructive schema operation is allowed without explicit owner approval.

## Procedure

1. Capture current failed-release logs, monitor snapshot, outbox, claims and scheduler inventory.
2. Keep the transport kill switch enabled.
3. Restore the recorded prior Compose digest and checked host bundle; do not edit application code or units on the VPS and never substitute `latest` for the recorded digest.
4. If state restoration is required, restore only the encrypted selective snapshot to a clean path and verify manifest/SQLite/JSON checksums.
5. Reconcile Zoho Sent, Zoho Drafts, Google Sheets and recipient/deal bindings before clearing the restore gate.
6. Confirm there are no unresolved `outcome_unknown` operations.
7. Start exactly one canonical scheduler per poller.
8. Run offline smoke checks and a controlled recipient test; confirm final offers remain draft-only.
9. Verify heartbeats, locks, no duplicate sends, active commit/tag/digest and skill SHA-256.
10. Document the rollback result and keep the failed release disabled.

The rollback command uses the recorded release environment and prior bundle:

```bash
sudo docker compose --env-file /etc/hermes-rfq-rollback.env \
  -f /docker/hermes-agent/rollback/docker-compose.yml up -d
```

If any controlled message may have reached Zoho after the rollback snapshot, do not start a poller until Sent, Drafts, Message-ID, thread and durable markers have been reconciled.

## Abort conditions

Do not resume transport when reconciliation is ambiguous, the backup checksum fails, SQLite integrity is not `ok`, Sheets state has no safe previous version, scheduler inventory is non-canonical, or the deployed digest is not the recorded rollback digest.
