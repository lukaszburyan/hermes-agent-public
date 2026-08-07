import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIL = ROOT / "scripts" / "orchesta-rfq-mail-poller.sh"
SHEETS = ROOT / "scripts" / "google-sheets-lead-poller.sh"
MAIL_POLLER = ROOT / "execution" / "zoho_mail_poller.py"
SHEETS_POLLER = ROOT / "execution" / "google_sheets_lead_poller.py"
TRANSPORT = ROOT / "execution" / "zoho_pre_offer_send.py"


def test_each_poller_has_one_canonical_entrypoint_and_its_own_lock():
    mail = MAIL.read_text(encoding="utf-8")
    sheets = SHEETS.read_text(encoding="utf-8")
    assert 'LOCK_FILE="$ROOT/.tmp/hermes-rfq-mail-poller.lock"' in mail
    assert 'LOCK_FILE="$ROOT/.tmp/google-sheets-lead-poller.lock"' in sheets
    assert "hermes-rfq-mail-poller.lock" not in sheets
    assert "google-sheets-lead-poller.lock" not in mail
    assert "export HERMES_ALLOW_PRE_OFFER_SEND=1" not in mail
    assert "export HERMES_ALLOW_PRE_OFFER_SEND=1" not in sheets
    for script in (mail, sheets):
        assert '. "$ENV_FILE"' in script
        assert "POLLER_ARGS+=(--auto-send)" not in script
        assert "RESTORE_RECONCILIATION_REQUIRED" in script
        assert "exit 75" in script
        assert 'export HERMES_TRANSPORT_KILL_SWITCH="$RELEASE_TRANSPORT_KILL_SWITCH"' in script
        assert 'export HERMES_OPERATIONAL_AUTOSEND_ENABLED="$RELEASE_OPERATIONAL_AUTOSEND_ENABLED"' in script
        assert 'export HERMES_TEST_MODE="$RELEASE_TEST_MODE"' in script
        assert 'export HERMES_TEST_RECIPIENT_ALLOWLIST="$RELEASE_TEST_RECIPIENT_ALLOWLIST"' in script
        assert "export ORCHESTA_RFQ_ALLOW_EXTERNAL_VISION=0" in script
        assert "export ORCHESTA_RFQ_ALLOW_SCHEMA_MODEL=0" in script
        assert "unset OPENAI_API_KEY" in script
        assert 'RECIPIENT="${HERMES_INTERNAL_NOTIFY_EMAIL:-}"' in script
        assert "HERMES_INTERNAL_NOTIFY_EMAIL is required" in script
    assert 'TARGET_EMAIL="${ZOHO_MAIL_ACCOUNT_EMAIL:' in mail
    assert '--target-email "$TARGET_EMAIL"' in mail
    assert 'rfq-mailbox@example.invalid' not in mail
    environment_template = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "HERMES_INTERNAL_NOTIFY_EMAIL=" in environment_template
    assert "HERMES_GOOGLE_SHEETS_SPREADSHEET_ID=" in environment_template
    for poller in (MAIL_POLLER, SHEETS_POLLER):
        assert "operational_autosend_enabled" in poller.read_text(encoding="utf-8")
    assert "HERMES_TRANSPORT_KILL_SWITCH" in TRANSPORT.read_text(encoding="utf-8")


def test_compose_mounts_runtime_environment_for_canonical_wrappers():
    compose = (ROOT / "infrastructure" / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${HERMES_RUNTIME_ENV_FILE}:/opt/data/.env:ro" in compose


def test_legacy_wrappers_immediately_delegate_to_the_canonical_entrypoints():
    wrappers = {
        ROOT / "scripts" / "run_mail_poller.sh": 'exec "$ROOT/scripts/orchesta-rfq-mail-poller.sh" "$@"',
        ROOT / "deploy" / "run_mail_poller.sh": 'exec "$ROOT/scripts/orchesta-rfq-mail-poller.sh" "$@"',
        ROOT / "scripts" / "run_poller.sh": 'exec "$ROOT/scripts/google-sheets-lead-poller.sh" "$@"',
    }
    for path, delegation in wrappers.items():
        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line and (not line.startswith("#") or line.startswith("#!"))
        ]
        assert lines == ["#!/usr/bin/env bash", "set -euo pipefail", 'ROOT="/opt/data"', delegation]


def test_packaged_root_cron_contains_no_poller_scheduler():
    cron = (ROOT / "infrastructure" / "cron" / "root.crontab").read_text(encoding="utf-8")
    assert "/opt/data/scripts/orchesta-rfq-mail-poller.sh" not in cron
    assert "/opt/data/scripts/google-sheets-lead-poller.sh" not in cron
    assert "execution/zoho_mail_poller.py" not in cron


def test_legacy_internal_poller_jobs_are_committed_disabled():
    jobs = json.loads((ROOT / "cron" / "jobs.json").read_text(encoding="utf-8"))["jobs"]
    poller_jobs = [
        job
        for job in jobs
        if job.get("script") in {
            "orchesta-rfq-mail-poller.sh",
            "google-sheets-lead-poller.sh",
        }
    ]
    assert len(poller_jobs) == 2
    assert all(job["enabled"] is False for job in poller_jobs)
    assert all(job["state"] == "paused" for job in poller_jobs)
    assert all(
        job["paused_reason"] == "superseded_by_canonical_systemd_timer"
        for job in poller_jobs
    )


def test_packaged_systemd_timers_use_one_canonical_entrypoint_per_poller():
    expected = {
        "mail-poller": "/opt/data/scripts/orchesta-rfq-mail-poller.sh",
        "google-sheets-poller": "/opt/data/scripts/google-sheets-lead-poller.sh",
    }
    for stem, entrypoint in expected.items():
        service = (ROOT / "infrastructure" / "systemd" / f"hermes-rfq-{stem}.service").read_text(
            encoding="utf-8"
        )
        timer = (ROOT / "infrastructure" / "systemd" / f"hermes-rfq-{stem}.timer").read_text(
            encoding="utf-8"
        )
        assert service.count(entrypoint) == 1
        assert "EnvironmentFile=/etc/hermes-rfq-release.env" in service
        assert f"Unit=hermes-rfq-{stem}.service" in timer


def test_scheduler_inventory_checks_internal_jobs_user_crons_and_exact_container_image():
    inventory = (
        ROOT / "infrastructure" / "host-scripts" / "hermes-rfq-scheduler-inventory"
    ).read_text(encoding="utf-8")
    assert "/var/spool/cron/crontabs" in inventory
    assert "internal_scheduler_hits" in inventory
    assert '"hermes-rfq-state-backup.timer"' in inventory
    assert '"hermes-rfq-release-monitor.timer"' in inventory
    assert "exact_image_match" in inventory
    assert '"docker",\n        "inspect"' in inventory


def test_runtime_services_use_the_release_environment_and_exact_container():
    systemd = ROOT / "infrastructure" / "systemd"
    gateway_service = (systemd / "hermes-gateway.service").read_text(encoding="utf-8")
    gateway_runner = (ROOT / "infrastructure" / "host-scripts" / "hermes-gateway-run.sh").read_text(
        encoding="utf-8"
    )
    audit_service = (systemd / "hermes-vps-daily-audit.service").read_text(encoding="utf-8")
    audit_runner = (ROOT / "infrastructure" / "host-scripts" / "hermes-vps-daily-host-audit").read_text(
        encoding="utf-8"
    )

    assert "EnvironmentFile=/etc/hermes-rfq-release.env" in gateway_service
    assert "${HERMES_CONTAINER_NAME:" in gateway_runner
    assert "docker inspect" in gateway_runner
    assert "ancestor=" not in gateway_runner
    assert ":latest" not in gateway_runner
    assert "EnvironmentFile=/etc/hermes-rfq-release.env" in audit_service
    assert "${HERMES_RUNTIME_DIR}" in audit_service
    assert "HERMES_RUNTIME_DIR" in audit_runner
    assert "<SET_ME" not in audit_service + audit_runner


def test_release_environment_and_ci_bundle_cover_every_host_runtime_input():
    release_environment = (
        ROOT / "infrastructure" / "systemd" / "hermes-rfq-release.env.example"
    ).read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for name in (
        "HERMES_CONTAINER_NAME",
        "HERMES_RUNTIME_DIR",
        "HERMES_RUNTIME_ENV_FILE",
        "HERMES_RCLONE_CONFIG_FILE",
        "HERMES_RELEASE_IMAGE",
        "HERMES_RELEASE_DIGEST",
        "HERMES_TRANSPORT_KILL_SWITCH",
        "HERMES_OPERATIONAL_AUTOSEND_ENABLED",
        "HERMES_TEST_MODE",
        "HERMES_TEST_RECIPIENT_ALLOWLIST",
    ):
        assert f"{name}=" in release_environment
    assert "hermes-host-release-${{ github.sha }}.tar.gz" in workflow
    assert "pytest-stability-runs-2-to-10.log" in workflow
    assert "for run in $(seq 2 10)" in workflow
    assert "infrastructure/docker/docker-compose.yml" in workflow
    assert "infrastructure/host-scripts" in workflow
    assert "infrastructure/systemd" in workflow
    assert "restore/restore-hermes.sh" in workflow
    assert "ROLLBACK_RUNBOOK.md" in workflow
    assert "mypy --scripts-are-modules" in workflow
    assert "sha256sum --check" in (ROOT / "DEPLOYMENT_RUNBOOK.md").read_text(encoding="utf-8")


def test_active_skill_documents_systemd_only_scheduler_and_restore_gate():
    skill = (ROOT / "skills" / "mail-lead-pipeline" / "SKILL.md").read_text(encoding="utf-8")
    assert "hermes-rfq-mail-poller.timer" in skill
    assert "hermes-rfq-google-sheets-poller.timer" in skill
    assert "RESTORE_RECONCILIATION_REQUIRED" in skill
    assert "dedicated mailbox poller cron" not in skill


def test_active_skill_and_prd_describe_split_message_policy_without_legacy_global_draft_mode():
    active_documents = [
        ROOT / "skills" / "mail-lead-pipeline" / "SKILL.md",
        ROOT / "skills" / "mail-lead-pipeline" / "references" / "business-profile.md",
        ROOT / "skills" / "mail-lead-pipeline" / "references" / "live-mailbox-validation.md",
        ROOT / "skills" / "mail-lead-pipeline" / "references" / "classification.md",
        ROOT
        / "skills"
        / "mail-lead-pipeline"
        / "references"
        / "adaptive-lead-response-composition.md",
        ROOT / "docs" / "mail-lead-pipeline-prd.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in active_documents).lower()
    assert "draft-first" not in combined
    assert "it does not send emails automatically" not in combined
    assert "never send customer email during validation" not in combined
    assert "maximum three" not in combined
    assert "approved operational" in combined
    assert "final offer" in combined
    assert "never automatically send a final offer" in combined
