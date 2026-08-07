---
name: hermes-gateway-troubleshooting
description: "Troubleshoot Hermes messaging gateway issues, especially Telegram bot silence, pairing failures, duplicate gateway conflicts, and Docker/systemd status mismatches."
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [hermes, gateway, telegram, troubleshooting, docker]
---

# hermes-gateway-troubleshooting

Use when Telegram bot or Hermes gateway does not respond, pairing fails, messages are not delivered, or the gateway service is unhealthy.

## Inputs

- SSH or Hostinger terminal access.
- Authorized Telegram User ID, if allowlist needs verification.
- Bot token only if configuration repair is required. Never print the token.

## Workflow

1. Run `execution/hermes-gateway-troubleshoot.sh` if available.
2. Check `hermes gateway status`, but do **not** treat it as authoritative by itself when Hermes runs in Docker/foreground mode.
3. Check `hermes pairing list`.
4. Check system and user systemd logs when systemd is available; in containers where `systemctl` is absent, inspect process list and gateway logs instead.
5. Check Docker container status/logs if Hermes runs in Docker.
6. Inspect running Hermes processes (`ps -ef | grep hermes`) and gateway lock files under the Hermes state dir. If logs say `Telegram bot token already in use (PID N)` and PID N is a live `hermes ... gateway run` process, the “conflict” may simply mean another valid gateway instance is already connected.
7. Verify token presence without displaying the token value.
8. Verify allowlist only includes approved Telegram User IDs.
9. Before restarting/killing anything, prove whether the live gateway is functional: check recent inbound log lines and send a harmless Telegram test message to the known home channel if the messaging tool is available.
10. Restart gateway only after reviewing logs and confirming the live process is stale or unhealthy: `execution/hermes-gateway-troubleshoot.sh --restart`.
11. Send test message to the bot and verify response.

## Pitfalls

- `hermes gateway status` may report “not running” when the gateway is actually running as a foreground process in a Docker container. Cross-check with process list, logs, lock files, and an actual Telegram send/receive test before reporting failure.
- Repeated `Telegram bot token already in use` log entries are not always an outage. If the referenced PID is live and receiving messages, the broken component may be a duplicate restart loop or status probe, not the active gateway.
- Do not kill/restart a live gateway just to clear a status mismatch unless logs or message tests show it is unhealthy.
- Never print bot tokens or secrets while checking configuration.

## Output

Report:
- root cause or strongest suspicion,
- what was fixed,
- gateway status,
- allowlist status,
- whether an actual Telegram send/receive test passed,
- remaining issue if any.

Do not ask Łukasz to debug manually when SSH/panel access is available.
