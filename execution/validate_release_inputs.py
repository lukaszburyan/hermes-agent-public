#!/usr/bin/env python3
"""Validate tracked structured files and release pinning invariants."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_COMPOSE = ROOT / "infrastructure/docker/docker-compose.yml"
COMPOSE_SHIM = ROOT / "infrastructure/docker/docker-compose.pinned.yml"
DOCKERFILE = ROOT / "infrastructure/docker/Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
MAIL_SELF_TEST_FIXTURE = "tests/fixtures/mail-lead-pipeline/zoho_poll_sample.json"
DEPLOYABLE_INFRA_DIRS = (
    ROOT / "infrastructure" / "host-scripts",
    ROOT / "infrastructure" / "systemd",
)


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
    )
    return [ROOT / value.decode("utf-8") for value in output.split(b"\0") if value]


def validate() -> dict[str, Any]:
    errors: list[str] = []
    checked = {"yaml": 0, "json": 0, "locks": 0, "compose": 0, "dockerfile": 0}
    for path in tracked_files():
        relative = path.relative_to(ROOT)
        try:
            if path.suffix.lower() in {".yaml", ".yml"}:
                list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
                checked["yaml"] += 1
            elif path.suffix.lower() == ".json":
                json.loads(path.read_text(encoding="utf-8"))
                checked["json"] += 1
        except (OSError, ValueError, yaml.YAMLError, json.JSONDecodeError) as exc:
            errors.append(f"{relative}: {exc.__class__.__name__}: {exc}")

        if path.name.endswith(".lock"):
            text = path.read_text(encoding="utf-8")
            if ">=" in text or "<=" in text or re.search(r"(?m)^[A-Za-z0-9_.-]+\s*[<>~!]", text):
                errors.append(f"{relative}: dependency version is not exact")
            if "/Users/" in text or "/home/" in text:
                errors.append(f"{relative}: machine-local path in generated lock")
            checked["locks"] += 1

    compose = yaml.safe_load(CANONICAL_COMPOSE.read_text(encoding="utf-8"))
    image = str(compose.get("services", {}).get("hermes-agent", {}).get("image", ""))
    if ":latest" in image or "@${HERMES_RELEASE_DIGEST" not in image:
        errors.append("canonical Compose image must require HERMES_RELEASE_DIGEST and must not use latest")
    checked["compose"] += 1

    shim = yaml.safe_load(COMPOSE_SHIM.read_text(encoding="utf-8"))
    includes = shim.get("include", []) if isinstance(shim, dict) else []
    if includes != [{"path": "docker-compose.yml"}]:
        errors.append("docker-compose.pinned.yml must remain a compatibility include of the canonical Compose")

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    from_instructions = [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    pinned_from = re.compile(
        r"FROM\s+\S+@sha256:[0-9a-f]{64}(?:\s+AS\s+[A-Za-z0-9_.-]+)?",
        re.IGNORECASE,
    )
    if not from_instructions or any(not pinned_from.fullmatch(line) for line in from_instructions):
        errors.append("Every Dockerfile FROM image must be pinned to a sha256 digest")
    if ":latest" in dockerfile:
        errors.append("Dockerfile must not reference latest")
    if f"COPY {MAIL_SELF_TEST_FIXTURE} " not in dockerfile:
        errors.append("Dockerfile must copy the deterministic mailbox self-test fixture")
    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    if f"!{MAIL_SELF_TEST_FIXTURE}" not in dockerignore:
        errors.append(".dockerignore must include the deterministic mailbox self-test fixture")
    checked["dockerfile"] += 1

    for directory in DEPLOYABLE_INFRA_DIRS:
        for path in directory.iterdir():
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            relative = path.relative_to(ROOT)
            if "<SET_ME" in text:
                errors.append(f"{relative}: unresolved deployment placeholder")
            if "ancestor=ghcr.io/hostinger/hvps-hermes-agent:latest" in text:
                errors.append(f"{relative}: runtime service must not discover a latest-tag container")

    return {"status": "ok" if not errors else "error", "checked": checked, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = validate()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
