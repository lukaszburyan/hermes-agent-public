#!/usr/bin/env bash
set -euo pipefail

REPORT_FILE="${HERMES_VPS_DAILY_REPORT:-/opt/data/reports/vps-daily-latest.txt}"

if [[ ! -s "$REPORT_FILE" ]]; then
  echo "Hermes VPS - raport dzienny"
  echo "Status: UWAGA"
  echo "Hostowy audyt nie utworzyl raportu. Sprawdz hermes-vps-daily-audit.timer."
  exit 1
fi

now="$(date +%s)"
modified="$(stat -c %Y "$REPORT_FILE")"
age_seconds=$((now - modified))

if (( age_seconds > 10800 )); then
  echo "Hermes VPS - raport dzienny"
  echo "Status: UWAGA"
  echo "Raport hosta jest starszy niz 3 godziny. Sprawdz hermes-vps-daily-audit.timer."
  exit 1
fi

# Hostowy audyt widzi tylko systemd i moze oznaczyc gateway jako nieaktywny,
# mimo ze Hermes dziala prawidlowo jako proces foreground w kontenerze Docker.
# Przed dostarczeniem raportu korygujemy ten jeden wynik na podstawie live statusu
# wewnatrz kontenera. Pozostale uwagi z audytu hosta sa zachowywane bez zmian.
gateway_running=0
if command -v hermes >/dev/null 2>&1; then
  gateway_status="$(hermes gateway status 2>&1 || true)"
  if [[ "$gateway_status" == *"Gateway is running"* ]]; then
    gateway_running=1
  fi
fi

HERMES_GATEWAY_RUNNING="$gateway_running" python3 - "$REPORT_FILE" <<'PY'
import os
import re
import sys
from pathlib import Path

report_path = Path(sys.argv[1])
text = report_path.read_text(encoding="utf-8", errors="replace")

if os.environ.get("HERMES_GATEWAY_RUNNING") != "1":
    print(text, end="" if text.endswith("\n") else "\n")
    raise SystemExit(0)

text = re.sub(
    r"gateway=inactive\b",
    "gateway=active (Docker/foreground)",
    text,
)

false_alert = "- Hermes Telegram gateway nie dziala."
lines = [line for line in text.splitlines() if line.strip() != false_alert]

try:
    attention_index = next(
        index for index, line in enumerate(lines) if line.strip() == "Co wymaga uwagi:"
    )
except StopIteration:
    attention_index = -1

if attention_index >= 0 and not any(
    line.strip() for line in lines[attention_index + 1 :]
):
    lines = lines[:attention_index] + ["Brak uwag."]
    lines = ["Status: OK" if line.strip() == "Status: UWAGA" else line for line in lines]

print("\n".join(lines))
PY
