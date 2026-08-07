from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infrastructure" / "host-scripts" / "hermes-rfq-scheduler-inventory"
LOADER = importlib.machinery.SourceFileLoader("hermes_scheduler_inventory", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None and SPEC.loader is not None
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def completed(command: list[str], stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr="")


def test_inventory_allows_auxiliary_timers_but_finds_internal_legacy_job(
    monkeypatch,
    tmp_path: Path,
):
    runtime = tmp_path / "runtime"
    jobs = runtime / "cron" / "jobs.json"
    jobs.parent.mkdir(parents=True)
    jobs.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "id": "legacy-mail",
                        "script": "orchesta-rfq-mail-poller.sh",
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    expected_image = "ghcr.io/example/hermes@sha256:" + "1" * 64
    monkeypatch.setenv("HERMES_CONTAINER_NAME", "hermes-agent-1")
    monkeypatch.setenv("HERMES_RELEASE_IMAGE", "ghcr.io/example/hermes")
    monkeypatch.setenv("HERMES_RELEASE_DIGEST", "sha256:" + "1" * 64)
    monkeypatch.setattr(inventory, "cron_text", lambda: "")

    def fake_run(command: list[str]):
        joined = " ".join(command)
        if command[:2] == ["systemctl", "cat"]:
            script = (
                "/opt/data/scripts/orchesta-rfq-mail-poller.sh"
                if "mail-poller" in command[-1]
                else "/opt/data/scripts/google-sheets-lead-poller.sh"
            )
            return completed(command, f"ExecStart={script}\n")
        if command[:2] == ["systemctl", "is-active"]:
            return completed(command, "active\n")
        if command[:2] == ["systemctl", "is-enabled"]:
            return completed(command, "enabled\n")
        if command[:3] == ["systemctl", "list-unit-files", "--type=timer"]:
            return completed(
                command,
                "\n".join(
                    [
                        "hermes-rfq-mail-poller.timer enabled",
                        "hermes-rfq-google-sheets-poller.timer enabled",
                        "hermes-rfq-state-backup.timer enabled",
                        "hermes-rfq-release-monitor.timer enabled",
                        "hermes-vps-daily-audit.timer enabled",
                    ]
                ),
            )
        if command[:3] == ["ps", "-eo", "args="]:
            return completed(command, "")
        if command[:2] == ["docker", "ps"]:
            return completed(command, "hermes-agent-1\n")
        if command[:2] == ["docker", "inspect"]:
            return completed(command, f"true|{expected_image}|sha256:{'2' * 64}\n")
        raise AssertionError(joined)

    monkeypatch.setattr(inventory, "run", fake_run)
    report = inventory.collect(runtime)

    assert report["release_container"]["exact_image_match"] is True
    assert report["legacy_scheduler_hits"] == [
        "internal:legacy-mail:orchesta-rfq-mail-poller.sh"
    ]


def test_release_container_snapshot_fails_closed_on_digest_mismatch(monkeypatch):
    monkeypatch.setenv("HERMES_CONTAINER_NAME", "hermes-agent-1")
    monkeypatch.setenv("HERMES_RELEASE_IMAGE", "ghcr.io/example/hermes")
    monkeypatch.setenv("HERMES_RELEASE_DIGEST", "sha256:" + "1" * 64)

    def fake_run(command: list[str]):
        if command[:2] == ["docker", "ps"]:
            return completed(command, "hermes-agent-1\n")
        return completed(
            command,
            "true|ghcr.io/example/hermes@sha256:" + "3" * 64 + "|sha256:" + "4" * 64,
        )

    monkeypatch.setattr(inventory, "run", fake_run)
    report = inventory.release_container_snapshot()
    assert report["running"] is True
    assert report["active_count"] == 1
    assert report["exact_image_match"] is False
