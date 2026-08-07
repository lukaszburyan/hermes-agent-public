#!/usr/bin/env bash
set -euo pipefail

echo "== Hermes Security Audit =="
date
echo

echo "== Identity =="
whoami || true
hostname || true
echo

echo "== Firewall =="
if command -v ufw >/dev/null 2>&1; then
  sudo ufw status verbose || true
else
  echo "ufw: not installed"
fi
if command -v firewall-cmd >/dev/null 2>&1; then
  sudo firewall-cmd --list-all || true
fi
echo

echo "== SSH effective config =="
if command -v sshd >/dev/null 2>&1; then
  sudo sshd -T 2>/dev/null | grep -E '^(port|permitrootlogin|passwordauthentication|pubkeyauthentication|allowusers|authenticationmethods)' || true
else
  echo "sshd: not available"
fi
echo

echo "== Open ports =="
if command -v ss >/dev/null 2>&1; then
  sudo ss -tulpn || true
else
  sudo netstat -tulpn 2>/dev/null || true
fi
echo

echo "== Sudo users =="
getent group sudo || true
getent group wheel || true
echo

echo "== Docker ports =="
if command -v docker >/dev/null 2>&1; then
  docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}" || true
else
  echo "docker: not installed"
fi
echo

echo "== Hostinger backups =="
echo "Check Hostinger dashboard/API for backup status. Do not infer from VPS shell alone."
