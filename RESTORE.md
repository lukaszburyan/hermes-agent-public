# Restore Hermes

## 1. Prepare the host

Install Docker Engine and Docker Compose. Retrieve the approved commit, tag,
CI image digest and checked host bundle from a private production or recovery
repository.

Use the only canonical Compose file:

```text
infrastructure/docker/docker-compose.yml
```

Set `HERMES_RELEASE_DIGEST` to the recorded `sha256` digest. `latest` is forbidden.

## 2. Run the restore dry run

```bash
./restore/restore-hermes.sh
```

Review every destination. On a new server, apply the file restore with:

```bash
sudo ./restore/restore-hermes.sh --apply
```

The script refuses to overwrite a non-empty Hermes data directory.

## 3. Restore credentials locally

On the target host:

1. Restore the mode-`0600` runtime environment without `OPENAI_API_KEY`.
2. Restore `auth.json`, Google OAuth files and rclone configuration from their approved secret store. Put `rclone.conf` inside a dedicated mode-`0700` directory and mount that directory, not the individual file, so OAuth refresh can replace the config atomically.
3. Reconnect Hermes-managed OpenAI Codex OAuth and Google/Zoho integrations.
4. Re-pair Telegram and verify its allowlist.
5. Run `hermes-release-preflight` before starting any service.

Do not commit the completed files.

## 4. Optional host integration

To install the saved systemd units and host helper scripts:

```bash
sudo ./restore/restore-hermes.sh --apply --install-host-units
```

Inspect paths and ports before enabling services. The script performs
`systemctl daemon-reload`, but it does not enable, start or restart anything.

## 5. Rebuild dependencies

Python dependencies are part of the digest-pinned image. Do not install packages
manually on the VPS. Verify the release environment with:

```bash
sudo /usr/local/sbin/hermes-release-preflight /etc/hermes-rfq-release.env
```

## 6. Validate before production

Verify the downloaded host bundle checksum, then run:

```bash
sha256sum --check hermes-host-release-<COMMIT>.tar.gz.sha256
sudo docker compose --env-file /etc/hermes-rfq-release.env \
  -f infrastructure/docker/docker-compose.yml config --quiet
sudo /usr/local/sbin/hermes-release-preflight /etc/hermes-rfq-release.env
```

After credentials are present, start the Compose project manually and verify:

- `hermes --version` and `hermes doctor`;
- Telegram allowlist and gateway response;
- `rfq-final-offer` self-test and PDF render;
- Zoho poller in shadow mode;
- exactly one systemd timer per poller and no legacy poller cron;
- dashboard remains bound to localhost;
- firewall and SSH settings match the saved snapshots.
