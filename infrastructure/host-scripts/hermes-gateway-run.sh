#!/usr/bin/env bash
set -euo pipefail
container="${HERMES_CONTAINER_NAME:?set HERMES_CONTAINER_NAME in /etc/hermes-rfq-release.env}"
running="$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)"
if [[ "$running" != "true" ]]; then
  echo "Hermes container is not running: $container" >&2
  exit 2
fi
exec docker exec -u hermes -e HERMES_HOME=/opt/data "$container" hermes --provider openai-codex --model openai-codex/gpt-5.6-sol gateway run --replace
