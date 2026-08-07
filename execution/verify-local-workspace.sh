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
  "skills/mail-lead-pipeline/SKILL.md"
  "skills/mail-lead-pipeline/references/business-profile.md"
  "skills/mail-lead-pipeline/references/classification.md"
  "skills/mail-lead-pipeline/references/draft-style.md"
  "skills/mail-lead-pipeline/references/calendar-availability.md"
  "skills/mail-lead-pipeline/references/research-and-crm.md"
  "skills/mail-lead-pipeline/references/telegram-escalation.md"
  "skills/mail-lead-pipeline/references/attachment-processing.md"
  "skills/mail-lead-pipeline/templates/crm-inbound-note.md"
  "directives/mail-lead-pipeline.md"
  "docs/mail-lead-pipeline-prd.md"
  "execution/mail-lead-pipeline-dry-run.py"
  "execution/attachment_router.py"
  "execution/rfq_attachment_extract.py"
  "execution/install-rfq-attachment-runtime.sh"
  "execution/requirements-rfq-attachments.txt"
  "execution/requirements-rfq-marker.txt"
  "tests/fixtures/mail-lead-pipeline/cases.json"
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

python3 "$ROOT/execution/attachment_router.py" --self-test >/dev/null

RFQ_PYTHON="${HERMES_RFQ_VENV_DIR:-}/bin/python"
if [[ -z "${HERMES_RFQ_VENV_DIR:-}" || ! -x "$RFQ_PYTHON" ]]; then
  if [[ -x "$ROOT/rfq-runtime/.venv/bin/python" ]]; then
    RFQ_PYTHON="$ROOT/rfq-runtime/.venv/bin/python"
  elif [[ -x "/opt/data/rfq-runtime/.venv/bin/python" ]]; then
    RFQ_PYTHON="/opt/data/rfq-runtime/.venv/bin/python"
  else
    RFQ_PYTHON="python3"
  fi
fi

"$RFQ_PYTHON" "$ROOT/execution/rfq_attachment_extract.py" --self-test >/dev/null

python3 "$ROOT/execution/mail-lead-pipeline-dry-run.py" \
  --fixtures "$ROOT/tests/fixtures/mail-lead-pipeline/cases.json" >/dev/null

if [[ -d "$ROOT/.git" ]]; then
  if git -C "$ROOT" ls-files --error-unmatch .env >/dev/null 2>&1; then
    echo "verify-local-workspace: .env is tracked by git" >&2
    exit 1
  fi
fi

echo "verify-local-workspace: ok"
