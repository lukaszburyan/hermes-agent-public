#!/usr/bin/env python3
"""Build and verify the release behavior manifest without reading secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


COMPONENT_GLOBS = {
    "dependency_locks": ("requirements*.txt", "requirements*.lock", "pyproject.toml", "poetry.lock"),
    "prompts": ("prompts/*.md", "prompts/*.txt"),
    "schemas": ("schemas/*.json",),
    "validator": ("execution/reply_validation.py", "execution/reply_contract.py", "execution/reply_sanitizer.py"),
    "message_policy": ("execution/message_policy.py", "MESSAGE_POLICY.md"),
    "rfq_skills": ("skills/mail-lead-pipeline/SKILL.md", "skills/rfq-final-offer/SKILL.md"),
    "tenant_config": ("tenants/**/*",),
    "wrappers": ("scripts/*rfq*.sh", "scripts/*sheets*.sh"),
    "systemd": ("infrastructure/systemd/hermes-rfq-*",),
    "runtime": (
        "execution/zoho_mail_poller.py", "execution/google_sheets_lead_poller.py",
        "execution/zoho_pre_offer_send.py", "execution/unified_lead_registry.py",
        "execution/restore_gate.py", "execution/response_claim_reaper.py",
        "execution/legacy_source_reconciliation.py",
        "infrastructure/docker/docker-compose.yml",
    ),
}
MODEL_CONFIG_KEYS = (
    "HERMES_LLM_REPLY_WRITER_ENABLED", "HERMES_LLM_INTENT_ENABLED", "HERMES_LLM_MODEL",
    "HERMES_OPERATIONAL_AUTOSEND_ENABLED", "HERMES_AUTO_REPLY_FOLLOWUPS",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def component_files(root: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for category, patterns in COMPONENT_GLOBS.items():
        matched: set[Path] = set()
        for pattern in patterns:
            matched.update(path for path in root.glob(pattern) if path.is_file())
        result[category] = {
            path.relative_to(root).as_posix(): sha256_file(path)
            for path in sorted(matched)
        }
    return result


def build_manifest(
    root: Path,
    *,
    commit: str,
    tag: str,
    image_digest: str = "",
    model_config: dict[str, str] | None = None,
) -> dict[str, Any]:
    components = component_files(root)
    config = dict(model_config or {key: os.environ.get(key, "") for key in MODEL_CONFIG_KEYS})
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "commit": str(commit),
        "tag": str(tag),
        "image_digest": str(image_digest),
        "components": components,
        "model_config": config,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest["manifest_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return manifest


def verify_manifest(
    manifest: dict[str, Any],
    root: Path,
    *,
    active_commit: str,
    active_tag: str,
    active_image_digest: str,
    active_model_config: dict[str, str] | None = None,
) -> dict[str, Any]:
    drift: list[str] = []
    if str(manifest.get("commit") or "") != str(active_commit or ""):
        drift.append("commit")
    if str(manifest.get("tag") or "") != str(active_tag or ""):
        drift.append("tag")
    expected_digest = str(manifest.get("image_digest") or "")
    if not expected_digest or expected_digest != str(active_image_digest or ""):
        drift.append("image_digest")
    expected_components = dict(manifest.get("components") or {})
    actual_components = component_files(root)
    if expected_components != actual_components:
        for category in sorted(set(expected_components) | set(actual_components)):
            if expected_components.get(category) != actual_components.get(category):
                drift.append(f"components:{category}")
    actual_model = dict(active_model_config or {key: os.environ.get(key, "") for key in MODEL_CONFIG_KEYS})
    if dict(manifest.get("model_config") or {}) != actual_model:
        drift.append("model_config")
    canonical_source = dict(manifest)
    supplied_sha = str(canonical_source.pop("manifest_sha256", ""))
    canonical = json.dumps(canonical_source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if supplied_sha != hashlib.sha256(canonical.encode("utf-8")).hexdigest():
        drift.append("manifest_sha256")
    return {"ok": not drift, "drift": sorted(set(drift)), "manifest_sha256": supplied_sha}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(
        args.root, commit=args.commit, tag=args.tag, image_digest=args.image_digest,
    )
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "manifest_sha256": manifest["manifest_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
