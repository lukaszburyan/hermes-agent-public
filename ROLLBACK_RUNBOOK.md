# Rollback runbook

## Current rollback point — 2026-08-09

The pre-RC11 rollback release is commit `5f64ca134494200405699069f85fd76ce1fc058a`, tag `v0.18.0-rc10`, image digest `sha256:5a177e5eff7eb11acaece1990dea17d4fef8d234752bfa6a6189d287bb559a0a`. Its latest scheduled encrypted off-site snapshot at release preparation has matching local/downloaded SHA-256 `3dbefb26cb7628eba51cd6b1af2aaa1e7bdbd308a059b804a4be0de20e1776d4`. Capture and record a newer pre-change snapshot immediately before deployment; if rollback is required, use that exact snapshot and keep transport paused until provider-evidence reconciliation passes. Never resend or recreate a final-offer draft.

## Preconditions

- Pollers are stopped and no RFQ process is active.
- The pre-deploy commit, tag and image digest are recorded.
- The pre-deploy encrypted state backup and checksum have been verified.
- The dedicated rclone configuration directory is writable so atomic OAuth refresh can persist; an error replacing `rclone.conf` means off-site backup evidence is invalid. A direct bind-mount of the file is forbidden because its rename fails even without `:ro`.
- Database rollback implications have been reviewed; no destructive schema operation is allowed without explicit owner approval.

## Procedure

1. Capture current failed-release logs, monitor snapshot, outbox, claims and scheduler inventory.
2. Keep the transport kill switch enabled.
3. Restore the recorded prior Compose digest and checked host bundle; do not edit application code or units on the VPS and never substitute `latest` for the recorded digest.
4. If state restoration is required, restore only the encrypted selective snapshot to a clean path and verify manifest/SQLite/JSON checksums.
5. Reconcile Zoho Sent, Zoho Drafts, Google Sheets and recipient/deal bindings before clearing the restore gate.
6. Confirm there are no unresolved `outcome_unknown` operations, active/expired claims, orphan `creating` artifacts, review states without tasks, terminal states with leases or source `done` without evidence.
7. Verify the prior behavior manifest against the exact prior image, then start exactly one canonical mail timer, one Sheets timer and one no-send claim-reaper timer.
8. Run offline smoke checks and a controlled recipient test; confirm final offers remain draft-only.
9. Verify heartbeats, locks, no duplicate sends, active commit/tag/digest, both RFQ skill hashes and behavior-manifest SHA-256.
10. Document the rollback result and keep the failed release disabled.

The rollback command uses the recorded release environment and prior bundle:

```bash
sudo docker compose --env-file /etc/hermes-rfq-rollback.env \
  -f /docker/hermes-agent/rollback/docker-compose.yml up -d
```

If any controlled message may have reached Zoho after the rollback snapshot, do not start a poller until Sent, Drafts, Message-ID, thread and durable markers have been reconciled.

## Abort conditions

Do not resume transport when reconciliation is ambiguous, provider Sent/Drafts visibility is unavailable, the backup checksum fails, SQLite integrity is not `ok`, Sheets state has no safe previous version, lifecycle inventory has blockers, scheduler inventory is non-canonical, the behavior manifest drifts, or the deployed digest is not the recorded rollback digest. A monitor-created `TRANSPORT_KILL_SWITCH` is removed only after the cause is resolved and evidence is recorded; it is never cleared automatically by a later green tick.
