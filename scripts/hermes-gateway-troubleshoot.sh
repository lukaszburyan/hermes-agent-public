#!/usr/bin/env bash
set -euo pipefail

DO_RESTART=0
if [[ "${1:-}" == "--restart" ]]; then
  DO_RESTART=1
fi

echo "== Hermes Gateway Troubleshooting =="
date
echo

echo "== Hermes command =="
command -v hermes || true
if command -v hermes >/dev/null 2>&1; then
  hermes gateway status || true
  hermes pairing list || true
fi
echo

echo "== Services =="
systemctl status hermes-gateway --no-pager 2>/dev/null | sed -n '1,80p' || true
systemctl --user status hermes-gateway --no-pager 2>/dev/null | sed -n '1,80p' || true
echo

echo "== Recent logs =="
journalctl -u hermes-gateway --no-pager -n 120 2>/dev/null || true
journalctl --user -u hermes-gateway --no-pager -n 120 2>/dev/null || true
echo

echo "== Docker =="
if command -v docker >/dev/null 2>&1; then
  docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" || true
  container="${HERMES_CONTAINER_NAME:-}"
  if [[ -n "$container" ]]; then
    docker logs "$container" --tail=200 || true
  else
    echo "Set HERMES_CONTAINER_NAME to include container logs."
  fi
fi
echo

if [[ "$DO_RESTART" -eq 1 ]]; then
  echo "== Restart =="
  if systemctl list-unit-files 2>/dev/null | grep -q '^hermes-gateway'; then
    sudo systemctl restart hermes-gateway
    sudo systemctl status hermes-gateway --no-pager | sed -n '1,40p'
  elif systemctl --user list-unit-files 2>/dev/null | grep -q '^hermes-gateway'; then
    systemctl --user restart hermes-gateway
    systemctl --user status hermes-gateway --no-pager | sed -n '1,40p'
  else
    echo "No hermes-gateway systemd unit found."
  fi
else
  echo "Run with --restart to restart the gateway after review."
fi
