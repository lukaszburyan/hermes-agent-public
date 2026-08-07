#!/usr/bin/env bash
set -euo pipefail

release_root="/opt/hermes-release/app"
runtime_root="/opt/data"

for release_dir in automations directives docs execution schemas scripts skills tenants; do
  ln -sfnT "$release_root/$release_dir" "$runtime_root/$release_dir"
done

ln -sfnT "$release_root/SOUL.md" "$runtime_root/SOUL.md"
ln -sfnT "$release_root/.env.example" "$runtime_root/.env.example"

printf '%s\n' "$HERMES_RELEASE_COMMIT" >"$runtime_root/HERMES_RELEASE_COMMIT"
printf '%s\n' "$HERMES_RELEASE_TAG" >"$runtime_root/HERMES_RELEASE_TAG"

exec /entrypoint.sh "$@"
