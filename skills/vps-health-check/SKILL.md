---
name: vps-health-check
description: "Use when Łukasz asks for VPS health, Hermes status, Docker status, gateway status, daily operational diagnostics, or whether this Hermes instance/profile has changed since installation."
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [vps, health-check, hermes, diagnostics, audit]
---

# vps-health-check

Use when Łukasz asks for VPS health, Hermes status, Docker status, gateway status, daily operational diagnostics, or whether the current Hermes installation/profile has been modified since install.

## Inputs

- SSH or Hostinger terminal access to the VPS.
- Telegram gateway configured if the report should be sent to Telegram.

## Workflow

1. Confirm whether you are on the host VPS or inside a Hermes/Docker container before interpreting OS/package/service results. Host reports may describe Ubuntu while the active terminal may be a Debian container.
2. Run `execution/vps-health-check.sh` on the VPS/host when host access is available.
3. Inspect CPU, RAM, disk, uptime, Docker, Hermes CLI, gateway service, and open ports.
4. Treat `systemctl` as only one signal for the gateway. In Docker/foreground deployments, `hermes-gateway.service` may correctly be inactive while `hermes gateway run` is live inside the container. Cross-check `hermes gateway status`, the live process, recent inbound gateway logs, and a harmless Telegram send test before reporting an outage.
5. If a host-generated report falsely marks `gateway=inactive` because it only checks systemd, fix the report generator or delivery wrapper to reconcile that field with the container's live `hermes gateway status`. Remove only the false gateway alert; preserve unrelated host warnings. Verify by running the actual scheduled report once and checking its saved output and delivery status.
6. For package-update findings, verify host-level privileges first (`whoami`, `sudo -n true`, `apt list --upgradable`, Ubuntu Pro/ESM status). Do not attempt to satisfy host Ubuntu/ESM updates from inside an unprivileged container; report the access blocker and request/obtain host SSH or panel/root access.
7. If Telegram is available, send a short Polish summary to the authorized home channel.
8. If any check fails, collect logs before restarting anything.

## Hermes instance change audit

Also use this skill when Łukasz asks whether the Hermes installation/profile has been modified since install, or whether changes were made by him versus by Hermes automation. Follow `references/hermes-instance-change-audit.md`:

1. Inspect live config/status/auth/cron.
2. List `.env` keys with values redacted.
3. Compare filesystem mtimes across `config.yaml`, `.env`, `auth.json`, `cron/`, `scripts/`, `execution/`, and `skills/`.
4. Check git status only if the install path is actually a git repo.
5. Search session history for user requests that triggered agent edits.
6. Distinguish confirmed user-requested agent changes from manual shell edits and from routine log/cache/session churn.

## Output

Short report:
- status: OK / issue,
- CPU/RAM/disk,
- uptime,
- Docker/Hermes/gateway status,
- last meaningful errors,
- next action.

For change audits, report:
- **Krótka odpowiedź**,
- **Co jest zmienione**,
- **Czy wygląda na ręczne zmiany Łukasza**,
- **Czego nie można potwierdzić**,
- **Wniosek**.

Never include secrets, tokens, passwords, or private keys.

<!-- obsidian-live-skills-test: 20260701T0612Z -->
