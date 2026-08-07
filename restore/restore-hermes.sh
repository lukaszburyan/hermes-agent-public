#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="/docker/hermes-agent/runtime"
DOCKER_TARGET="/docker/hermes-agent/release"
APPLY=0
INSTALL_HOST_UNITS=0

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=1
      ;;
    --install-host-units)
      INSTALL_HOST_UNITS=1
      ;;
    --target)
      shift
      TARGET="${1:?--target requires a path}"
      DOCKER_TARGET="$(dirname "$TARGET")/release"
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

echo "Hermes restore source: $ROOT"
echo "Hermes runtime target: $TARGET"
echo "Release configuration: $DOCKER_TARGET"
echo "Host units:           $INSTALL_HOST_UNITS"

if [[ "$APPLY" != "1" ]]; then
  echo "Dry run only. Re-run with sudo and --apply after reviewing RESTORE.md."
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run the apply phase as root." >&2
  exit 1
fi

if [[ -d "$TARGET" && -n "$(find "$TARGET" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo "Refusing to overwrite non-empty target: $TARGET" >&2
  exit 1
fi

install -d -m 0755 "$TARGET" "$DOCKER_TARGET"
for directory in cron state tmp logs reports backups sessions CRM rfq-runtime; do
  install -d -m 0700 "$TARGET/$directory"
done
install -m 0600 "$ROOT/config.yaml" "$TARGET/config.yaml"
install -m 0600 "$ROOT/.env.example" "$TARGET/.env.example"

install -m 0644 \
  "$ROOT/infrastructure/docker/docker-compose.yml" \
  "$DOCKER_TARGET/docker-compose.yml"
install -m 0600 "$ROOT/infrastructure/systemd/hermes-rfq-release.env.example" \
  "$DOCKER_TARGET/hermes-rfq-release.env.example"

if [[ "$INSTALL_HOST_UNITS" == "1" ]]; then
  install -m 0755 "$ROOT/infrastructure/host-scripts/hermes-gateway-run.sh" \
    /usr/local/bin/hermes-gateway-run.sh
  for script in hermes-vps-daily-host-audit hermes-rfq-scheduler-inventory hermes-release-preflight; do
    install -m 0755 "$ROOT/infrastructure/host-scripts/$script" "/usr/local/sbin/$script"
  done
  for unit in "$ROOT"/infrastructure/systemd/*.service "$ROOT"/infrastructure/systemd/*.timer; do
    install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
  done
  systemctl daemon-reload
fi

echo "Immutable runtime skeleton restored without credentials or application code."
echo "Next: restore encrypted state and approved secrets, install /etc/hermes-rfq-release.env, then run hermes-release-preflight."
