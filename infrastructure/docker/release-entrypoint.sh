#!/usr/bin/env bash
set -euo pipefail

release_root="/opt/hermes-release/app"
runtime_root="/opt/data"
image_commit="$(tr -d '\r\n' </opt/hermes-release/IMAGE_RELEASE_COMMIT)"
image_tag="$(tr -d '\r\n' </opt/hermes-release/IMAGE_RELEASE_TAG)"

if [[ "$HERMES_RELEASE_COMMIT" != "$image_commit" ]]; then
  printf '%s\n' "release metadata mismatch: commit" >&2
  exit 78
fi
if [[ "$HERMES_RELEASE_TAG" != "$image_tag" ]]; then
  printf '%s\n' "release metadata mismatch: tag" >&2
  exit 78
fi

for release_dir in automations directives docs execution schemas scripts skills tenants; do
  ln -sfnT "$release_root/$release_dir" "$runtime_root/$release_dir"
done

ln -sfnT "$release_root/SOUL.md" "$runtime_root/SOUL.md"
ln -sfnT "$release_root/.env.example" "$runtime_root/.env.example"

printf '%s\n' "$HERMES_RELEASE_COMMIT" >"$runtime_root/HERMES_RELEASE_COMMIT"
printf '%s\n' "$HERMES_RELEASE_TAG" >"$runtime_root/HERMES_RELEASE_TAG"

"/opt/hermes-release/.venv/bin/python" "$release_root/execution/behavior_manifest.py" \
  --root "$release_root" \
  --commit "$HERMES_RELEASE_COMMIT" \
  --tag "$HERMES_RELEASE_TAG" \
  --image-digest "$HERMES_RELEASE_DIGEST" \
  --output "$runtime_root/BEHAVIOR_MANIFEST.json"

exec /entrypoint.sh "$@"
