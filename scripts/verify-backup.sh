#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

required=(
  "README.md"
  "RESTORE.md"
  ".env.example"
  "config.yaml"
  "cron/jobs.json"
  "execution/zoho_mail_poller.py"
  "execution/hermes_rfq_core.py"
  "skills/rfq-final-offer/SKILL.md"
  "skills/rfq-final-offer/scripts/rfq_final_offer.py"
  "infrastructure/docker/docker-compose.yml"
  "infrastructure/docker/docker-compose.pinned.yml"
  "infrastructure/docker/.env.example"
  "infrastructure/systemd/hermes-vps-daily-audit.timer"
  "inventory/runtime-versions.txt"
  "inventory/manifest.sha256"
  "restore/restore-hermes.sh"
)

for path in "${required[@]}"; do
  if [[ ! -e "$ROOT/$path" ]]; then
    echo "verify-backup: missing $path" >&2
    exit 1
  fi
done

for forbidden in \
  "$ROOT/.env" \
  "$ROOT/auth.json" \
  "$ROOT/google_token.json" \
  "$ROOT/google_client_secret.json"; do
  if [[ -e "$forbidden" ]]; then
    echo "verify-backup: forbidden file present: $forbidden" >&2
    exit 1
  fi
done

if find "$ROOT" -type f -not -path "$ROOT/.git/*" \
  \( -name '*.pem' -o -name '*.key' -o -name '*.sqlite*' -o -name '*.db' -o -name '*token.json' \) \
  -print -quit | grep -q .; then
  echo "verify-backup: forbidden credential/runtime filename present" >&2
  exit 1
fi

"$ROOT/scripts/secret-scan.sh" "$ROOT"

if command -v gitleaks >/dev/null 2>&1; then
  gitleaks dir "$ROOT" --no-banner --redact --exit-code 1
else
  echo "verify-backup: gitleaks not installed; primary scan completed"
fi

python3 - "$ROOT" <<'PY'
import json
from pathlib import Path
import sys
import yaml

root = Path(sys.argv[1])
for path in (
    root / "config.yaml",
    root / "infrastructure/docker/docker-compose.yml",
    root / "infrastructure/docker/docker-compose.pinned.yml",
):
    yaml.safe_load(path.read_text())
json.loads((root / "cron/jobs.json").read_text())
PY

find "$ROOT" -type f -name '*.sh' -not -path "$ROOT/.git/*" -print0 \
  | xargs -0 -n1 bash -n
python3 -m py_compile \
  "$ROOT/tools/export-hermes-vps-backup.py" \
  "$ROOT/tools/generate-manifest.py"

(
  cd "$ROOT"
  shasum -a 256 -c inventory/manifest.sha256
)

echo "verify-backup: ok"
