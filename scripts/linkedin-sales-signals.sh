#!/usr/bin/env bash
set -euo pipefail
cd /opt/data/automations/linkedin-sales-signals
exec python3 /opt/data/automations/linkedin-sales-signals/run_linkedin_sales_signals.py
