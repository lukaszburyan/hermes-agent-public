#!/usr/bin/env bash
set -euo pipefail

echo "== Hermes VPS Health Check =="
date
echo

echo "== Host =="
whoami || true
hostname || true
uptime || true
uname -a || true
if command -v lsb_release >/dev/null 2>&1; then
  lsb_release -a || true
fi
echo

echo "== Disk =="
df -h || true
echo

echo "== Memory =="
free -h || true
echo

echo "== CPU =="
if command -v top >/dev/null 2>&1; then
  top -bn1 | sed -n '1,5p' || true
fi
echo

echo "== Docker =="
if command -v docker >/dev/null 2>&1; then
  docker --version || true
  docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" || true
else
  echo "docker: not installed"
fi
echo

echo "== Hermes =="
if command -v hermes >/dev/null 2>&1; then
  command -v hermes
  hermes doctor || true
  hermes gateway status || true
else
  echo "hermes: not installed in this shell"
fi
echo

echo "== Gateway service =="
systemctl status hermes-gateway --no-pager 2>/dev/null | sed -n '1,25p' || true
systemctl --user status hermes-gateway --no-pager 2>/dev/null | sed -n '1,25p' || true
echo

echo "== Open ports =="
if command -v ss >/dev/null 2>&1; then
  ss -tulpn || true
else
  netstat -tulpn 2>/dev/null || true
fi
