# Hermes instance change audit

Use this reference when Łukasz asks whether the current Hermes instance was modified after installation, or whether changes were made by the user versus by Hermes/automation.

## Goal

Produce an evidence-based Polish summary that distinguishes:

- base/runtime Hermes installation,
- user/profile configuration under `HERMES_HOME` such as `/opt/data/config.yaml`, `.env`, `auth.json`, `cron/jobs.json`, `skills/`, `scripts/`, `execution/`, logs/cache/state,
- agent-executed changes requested by Łukasz,
- automated churn from cron, gateway, RFQ pipeline, caches, logs, and session DB,
- actual manual shell edits, only if supported by shell history, git metadata, file owners/mtimes, or session transcripts.

## Checklist

1. Load `hermes-agent` for current CLI paths and commands, but do not edit it because it is bundled/protected.
2. Check live Hermes status and config paths:
   - `hermes status --all`
   - `hermes config path`
   - `hermes config env-path`
   - `hermes auth list`
3. Inspect config without leaking secrets:
   - read `config.yaml` directly,
   - list only `.env` keys, redact all values.
4. Inspect scheduled jobs:
   - use the cron tool or `hermes cron list`,
   - read `cron/jobs.json` for `created_at`, scripts, schedules, last status.
5. Inspect filesystem evidence:
   - mtimes and sizes for `config.yaml`, `.env`, `auth.json`, `cron/jobs.json`, `scripts/`, `execution/`, `skills/`, logs/cache/state,
   - ignore routine churn from `logs/`, `state.db*`, `.npm/_cacache`, `.local/share/tirith`, `.tick.lock`, model cache unless relevant.
6. Check for source-code repo evidence:
   - if `/opt/hermes` is a git repo, run `git status --short` and recent `git log`,
   - if not a git repo, say so and do not imply a clean upstream diff.
7. Check manual-command evidence when available:
   - shell histories for relevant users, redacting secrets,
   - file ownership/ctime/mtime patterns,
   - session DB / `session_search` for user requests that triggered changes.
8. Search past sessions for explicit user requests around the changed area before asking Łukasz to repeat context.
9. Summarize with confidence labels: “confirmed”, “likely”, “not found / no evidence”.

## Reporting pattern

Keep it short and operational:

- **Krótka odpowiedź:** yes/no/unclear.
- **Co jest zmienione:** bullets with paths/components.
- **Czy wygląda na ręczne zmiany Łukasza:** only if evidence supports it.
- **Czego nie można potwierdzić:** missing git repo/history/log limitation.
- **Wniosek:** distinguish configured/extended instance from manual tampering.

## Pitfalls

- Do not print `.env` values, tokens, OAuth blobs, private keys, or refresh tokens.
- Do not call routine logs/cache/session DB writes “user modifications”.
- Do not claim “no code changes” when the install directory is not a git repo; say that a git diff was unavailable.
- Do not treat user-initiated agent work as manual shell editing unless shell/history evidence exists.
- Do not save PRs, commit SHAs, one-off file counts, or transient timestamps to memory.