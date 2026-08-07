#!/usr/bin/env bash
set -euo pipefail

TARGETS=("$@")
if [[ "${#TARGETS[@]}" -eq 0 ]]; then
  TARGETS=(.)
fi

EXCLUDES=(
  --exclude-dir=.git
  --exclude-dir=node_modules
  --exclude-dir=.venv
  --exclude-dir=venv
  --exclude-dir=.pytest_cache
  --exclude-dir=dist
  --exclude-dir=build
  --exclude-dir=.tmp
  --exclude-dir=outputs
  --exclude=.DS_Store
  --exclude=.env
)

PATTERNS=(
  "OpenAI API key|sk-[A-Za-z0-9_-]{20,}"
  "GitHub classic token|ghp_[A-Za-z0-9_]{20,}"
  "GitHub fine-grained token|github_pat_[A-Za-z0-9_]{20,}"
  "Private key|BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY"
  "Telegram bot token|[0-9]{8,12}:[A-Za-z0-9_-]{30,}"
  "Google API key|AIza[0-9A-Za-z_-]{30,}"
  "Slack token|xox[baprs]-[A-Za-z0-9-]{10,}"
  "JWT|eyJ[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}\\.[A-Za-z0-9_-]{10,}"
  "Generic env secret|(TOKEN|PASSWORD|SECRET|API_KEY)[[:space:]]*=[[:space:]]*[\"']?[A-Za-z0-9_./+=:@-]{16,}"
)

found=0
tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

for item in "${PATTERNS[@]}"; do
  label="${item%%|*}"
  pattern="${item#*|}"
  : > "$tmp_file"
  existing_targets=()
  for target in "${TARGETS[@]}"; do
    if [[ -e "$target" ]]; then
      existing_targets+=("$target")
    fi
  done
  if [[ "${#existing_targets[@]}" -eq 0 ]]; then
    continue
  fi
  if grep -RInE "${EXCLUDES[@]}" "$pattern" "${existing_targets[@]}" 2>/dev/null \
    | grep -Ev 'your_|placeholder|example|EXAMPLE|TOKEN_VALUE|PASSWORD_VALUE|AAA\\.\\.\\.|<[^>]+>|[xX]{8,}|generate-a-strong-secret|ENV_API_KEY' \
    | awk -F: -v label="$label" '{print label "\t" $1 ":" $2}' > "$tmp_file"; then
    if [[ -s "$tmp_file" ]]; then
      found=1
      cat "$tmp_file"
    fi
  fi
done

if [[ "$found" -ne 0 ]]; then
  echo "secret-scan: possible secrets found. Remove them or exclude the files before commit." >&2
  exit 1
fi

echo "secret-scan: no high-confidence secrets found"
