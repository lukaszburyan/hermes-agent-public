#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-${HERMES_HOME:-$(pwd)}}"
BRANCH="${HERMES_GIT_BRANCH:-main}"
REMOTE="${HERMES_GIT_REMOTE:-origin}"
DATE_STAMP="$(TZ=Europe/Warsaw date +%F)"
MESSAGE="${HERMES_GIT_MESSAGE:-nightly hermes sync $DATE_STAMP}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="${DRY_RUN:-0}"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
elif [[ -n "${HERMES_HOME:-}" && -f "$HERMES_HOME/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$HERMES_HOME/.env"
  set +a
fi

GIT_WITH_TOKEN=(git)
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  GIT_WITH_TOKEN=(
    git
    -c
    'credential.helper=!f() { echo username=x-access-token; echo password=$GITHUB_TOKEN; }; f'
  )
fi

cd "$ROOT"

if [[ ! -d .git ]]; then
  echo "nightly-github-sync: not a git repository: $ROOT" >&2
  exit 2
fi

if [[ ! -x "$SCRIPT_DIR/secret-scan.sh" ]]; then
  echo "nightly-github-sync: missing executable secret-scan.sh next to this script" >&2
  exit 2
fi

SCAN_TARGETS=(
  "$ROOT/.gitignore"
  "$ROOT/config.yaml"
  "$ROOT/SOUL.md"
  "$ROOT/cron"
  "$ROOT/memories"
  "$ROOT/scripts"
  "$ROOT/skills/vps-health-check"
  "$ROOT/skills/hermes-gateway-troubleshooting"
  "$ROOT/skills/nightly-github-sync"
  "$ROOT/skills/security-audit"
  "$ROOT/skills/open-dashboard-runbook"
  "$ROOT/skills/mail-lead-pipeline"
  "$ROOT/directives/mail-lead-pipeline.md"
  "$ROOT/docs/mail-lead-pipeline-prd.md"
)

"$SCRIPT_DIR/secret-scan.sh" "${SCAN_TARGETS[@]}"
git diff --check

if [[ -z "$(git status --porcelain)" ]]; then
  echo "nightly-github-sync: no changes"
  exit 0
fi

git status --short

if [[ "$DRY_RUN" == "1" ]]; then
  echo "nightly-github-sync: dry run, not committing or pushing"
  exit 0
fi

if ! "${GIT_WITH_TOKEN[@]}" ls-remote "$REMOTE" >/dev/null 2>&1; then
  echo "nightly-github-sync: remote is not reachable yet: $REMOTE"
  echo "nightly-github-sync: create the private repo or update GitHub token permissions, then rerun"
  exit 0
fi

git add -A
"$SCRIPT_DIR/secret-scan.sh" "${SCAN_TARGETS[@]}"
git commit -m "$MESSAGE"
git branch -M "$BRANCH"
"${GIT_WITH_TOKEN[@]}" push "$REMOTE" "$BRANCH"
