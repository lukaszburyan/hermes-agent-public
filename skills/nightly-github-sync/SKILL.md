# nightly-github-sync

Use to back up Hermes state to the private GitHub repo every night.

Target repo: `https://github.com/lukaszburyan/hermes-agent-public`

## Guardrails

Never commit:
- `.env`,
- tokeny,
- hasla,
- klucze SSH,
- pliki credentials,
- backup codes,
- sekrety API,
- dane kart.

Run the secret scan before every commit and push. If a possible secret is found, stop and alert Łukasz in Telegram.

## Workflow

1. Confirm current directory is the Hermes data directory.
2. Run `execution/secret-scan.sh .`.
3. Run `git diff --check`.
4. Review `git status --short`.
5. Commit only when changes are safe.
6. Push to `origin main`.

Command:

```bash
execution/nightly-github-sync.sh /path/to/hermes-data
```

Cron:

```cron
0 0 * * * TZ=Europe/Warsaw /path/to/execution/nightly-github-sync.sh /path/to/hermes-data >> /path/to/logs/nightly-github-sync.log 2>&1
```

Pause cron by commenting the line. Resume by uncommenting it.
