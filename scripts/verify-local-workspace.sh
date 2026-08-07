#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$(pwd)}"

required=(
  "AGENTS.md"
  "README.md"
  "directives/hermes-hostinger-setup.md"
  "docs/hermes-access-request.md"
  "docs/hermes-configuration-status.md"
  "USER.md"
  "MEMORY.md"
  "SOUL.md"
  "execution/secret-scan.sh"
  "execution/vps-health-check.sh"
  "execution/hermes-gateway-troubleshoot.sh"
  "execution/nightly-github-sync.sh"
  "execution/security-audit.sh"
  "skills/vps-health-check/SKILL.md"
  "skills/hermes-gateway-troubleshooting/SKILL.md"
  "skills/nightly-github-sync/SKILL.md"
  "skills/security-audit/SKILL.md"
  "skills/open-dashboard-runbook/SKILL.md"
)

missing=0
for path in "${required[@]}"; do
  if [[ ! -e "$ROOT/$path" ]]; then
    echo "missing: $path" >&2
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

"$ROOT/execution/secret-scan.sh" "$ROOT"

if [[ -d "$ROOT/.git" ]]; then
  if git -C "$ROOT" ls-files --error-unmatch .env >/dev/null 2>&1; then
    echo "verify-local-workspace: .env is tracked by git" >&2
    exit 1
  fi
fi

echo "verify-local-workspace: ok"
