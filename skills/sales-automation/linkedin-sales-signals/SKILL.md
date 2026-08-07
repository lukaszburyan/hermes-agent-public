---
name: linkedin-sales-signals
description: "Operate the weekly LinkedIn sales-signal automation for Łukasz: Apify actors, phrase registry, CSV generation, email delivery, and 14-day retention."
version: 1.0.0
author: Hermes Agent
metadata:
  hermes:
    tags: [linkedin, sales, apify, csv, email, cron]
---

# LinkedIn Sales Signals Automation

## When to use
Use when Łukasz asks to inspect, run, debug, modify, or extend the weekly automation named **Cotygodniowe sygnały sprzedażowe z LinkedIn**.

## Fixed locations
- Automation directory: `/opt/data/automations/linkedin-sales-signals`
- Full execution prompt/spec: `/opt/data/automations/linkedin-sales-signals/AUTOMATION_PROMPT.md`
- Phrase registry: `/opt/data/automations/linkedin-sales-signals/phrases.json`
- Runs: `/opt/data/automations/linkedin-sales-signals/runs/<run_id>/`
- Phrase stats: `/opt/data/automations/linkedin-sales-signals/phrase_stats.json`
- Email helper: `/opt/data/automations/linkedin-sales-signals/send_csv_email.py`
- Cron name: `Cotygodniowe sygnały sprzedażowe z LinkedIn`

## Cron
- Schedule: `0 8 * * 1`
- Timezone expectation: Europe/Warsaw
- Delivery: origin Telegram chat
- Current job ID should be obtained with `cronjob(action='list')`; do not guess it.
- Current job runs in `no_agent` script mode via wrapper `/opt/data/scripts/linkedin-sales-signals.sh`, which calls `/opt/data/automations/linkedin-sales-signals/run_linkedin_sales_signals.py`.

## Required credentials
- Apify: `APIFY_TOKEN` or `APIFY_API_TOKEN` available to the cron runtime, preferably in `/opt/data/.env`.
- Email: helper tries Resend, SMTP, then Zoho Mail API. Current environment has Zoho Mail credentials in `/opt/data/.env`.
- Never print or store credential values.

## Actors
- `harvestapi/linkedin-post-search`
- `harvestapi/linkedin-post-comments`
- `harvestapi/linkedin-post-reactions`
- Do not use `harvestapi/linkedin-profile-posts` for this process.

## Output CSV columns
1. URL oryginalnego posta
2. Treść oryginalnego posta
3. Autor posta
4. Sygnał sprzedażowy
5. URL profilu LinkedIn
6. Powód kwalifikacji
7. Rodzaj interakcji
8. Treść komentarza

## Operational checks
1. Read `AUTOMATION_PROMPT.md` before changing behavior.
2. Validate `phrases.json`: active phrases non-empty, unique, <=85 chars.
3. Validate scripts with `python3 -m py_compile /opt/data/automations/linkedin-sales-signals/run_linkedin_sales_signals.py /opt/data/automations/linkedin-sales-signals/send_csv_email.py`; optional non-send Zoho auth check can refresh token and list accounts without sending mail.
4. Use `cronjob(action='list')` to verify schedule and next run.
5. If Apify is missing, report it as the blocker; do not fabricate scraping results.
6. Interaction actors can run long. The script defaults to bounded interaction fetches (`LINKEDIN_INTERACTION_MAX_ITEMS=25`, one attempt per chunk, total budget about 300s) so CSV/email delivery is not blocked for hours. Partial interaction coverage must be reported in `run_log.json` and email body.

## Retention
Keep raw scraper data, CSVs, profile/comment/post contents, and run logs for max 14 days. Phrase effectiveness stats may retain only the minimum needed for the three-week zero-record rule.
