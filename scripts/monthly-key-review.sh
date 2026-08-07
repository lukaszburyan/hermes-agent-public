#!/usr/bin/env bash
set -euo pipefail
echo "Monthly Key Review - Europe/Warsaw"
echo
echo "Integrations to review:"
echo "- Hostinger VPS SSH: configured outside Hermes; value hidden"
echo "- Telegram bot token: configured; value hidden"
echo "- Telegram allowed users: configured; value hidden"
echo "- GitHub token: configured; value hidden"
echo "- OpenAI Codex OAuth: check auth status with hermes auth status openai-codex"
echo
echo "Action: rotate or revoke tokens that are no longer needed. Never paste token values into chat or docs."
