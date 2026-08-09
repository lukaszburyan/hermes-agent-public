from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "infrastructure" / "host-scripts" / "hermes-release-preflight"
LOADER = importlib.machinery.SourceFileLoader("hermes_release_preflight", str(SCRIPT))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None and SPEC.loader is not None
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def write(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


def fixture(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    for name in ("cron", "state", "tmp", "logs", "reports", "backups", "sessions", "CRM", "rfq-runtime"):
        (runtime / name).mkdir(parents=True, exist_ok=True)
    write(runtime / "config.yaml", "model: controlled\n")
    for name in ("auth.json", "google_client_secret.json", "google_token.json"):
        write(runtime / name, "{}\n")
    rclone_directory = runtime / "rclone"
    rclone_directory.mkdir(mode=0o700)
    write(rclone_directory / "rclone.conf", "[controlled]\ntype = local\n")
    runtime_values = {
        key: "controlled-value" for key in preflight.REQUIRED_RUNTIME_KEYS
    }
    runtime_values["HERMES_BACKUP_RETENTION_DELETE_APPROVED"] = "0"
    runtime_values["ORCHESTA_RFQ_ALLOW_EXTERNAL_VISION"] = "0"
    runtime_values["ORCHESTA_RFQ_ALLOW_SCHEMA_MODEL"] = "0"
    runtime_environment = runtime / ".env"
    write(runtime_environment, "\n".join(f"{key}={value}" for key, value in runtime_values.items()) + "\n")
    for database in (runtime / "state" / "state.sqlite3", runtime / "tmp" / "orchesta-rfq-unified.sqlite3"):
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE controlled(id INTEGER PRIMARY KEY)")
        database.chmod(0o600)
    write(runtime / "tmp" / "google-sheets-leads-state.json", "{}\n")
    release_environment = tmp_path / "release.env"
    release_values = {
        "HERMES_CONTAINER_NAME": "hermes-agent-hermes-agent-1",
        "HERMES_RUNTIME_DIR": str(runtime),
        "HERMES_RUNTIME_ENV_FILE": str(runtime_environment),
        "HERMES_RCLONE_CONFIG_DIR": str(rclone_directory),
        "HERMES_RCLONE_CONFIG_FILE": str(rclone_directory / "rclone.conf"),
        "HERMES_RELEASE_IMAGE": "ghcr.io/example/hermes-agent-public",
        "HERMES_RELEASE_COMMIT": "f" * 40,
        "HERMES_RELEASE_TAG": "v0.18.0-test",
        "HERMES_RELEASE_DIGEST": "sha256:" + "1" * 64,
        "HERMES_TRANSPORT_KILL_SWITCH": "1",
        "HERMES_OPERATIONAL_AUTOSEND_ENABLED": "0",
    }
    write(release_environment, "\n".join(f"{key}={value}" for key, value in release_values.items()) + "\n")
    return release_environment, runtime_environment


def test_release_preflight_accepts_complete_fail_closed_runtime(tmp_path: Path):
    release_environment, _ = fixture(tmp_path)
    report = preflight.collect(release_environment)
    assert report["status"] == "ok", report
    assert report["errors"] == []


def test_release_preflight_requires_explicit_valid_retention_choice(tmp_path: Path):
    release_environment, runtime_environment = fixture(tmp_path)
    runtime_text = runtime_environment.read_text(encoding="utf-8").replace(
        "HERMES_BACKUP_RETENTION_DELETE_APPROVED=0",
        "HERMES_BACKUP_RETENTION_DELETE_APPROVED=invalid",
    )
    write(runtime_environment, runtime_text)

    report = preflight.collect(release_environment)

    assert "backup_retention_delete_approval_invalid" in report["errors"]


def test_release_preflight_rejects_token_api_placeholder_digest_and_open_permissions(tmp_path: Path):
    release_environment, runtime_environment = fixture(tmp_path)
    release_text = release_environment.read_text(encoding="utf-8").replace(
        "sha256:" + "1" * 64,
        "sha256:" + "0" * 64,
    )
    write(release_environment, release_text)
    with runtime_environment.open("a", encoding="utf-8") as handle:
        handle.write("OPENAI_API_KEY=forbidden\n")
    runtime_environment.chmod(0o644)

    report = preflight.collect(release_environment)

    assert report["status"] == "error"
    assert "release_digest_invalid_or_placeholder" in report["errors"]
    assert "token_billed_openai_api_key_forbidden" in report["errors"]
    assert any(str(error).startswith("sensitive_file_permissions_too_open:") for error in report["errors"])
    json.dumps(report)


def test_release_preflight_rejects_missing_or_placeholder_commit_and_tag(tmp_path: Path):
    release_environment, _ = fixture(tmp_path)
    release_text = release_environment.read_text(encoding="utf-8")
    release_text = release_text.replace("HERMES_RELEASE_COMMIT=" + "f" * 40, "HERMES_RELEASE_COMMIT=" + "0" * 40)
    release_text = release_text.replace("HERMES_RELEASE_TAG=v0.18.0-test", "HERMES_RELEASE_TAG=v0.0.0-placeholder")
    write(release_environment, release_text)

    report = preflight.collect(release_environment)

    assert "release_commit_invalid_or_placeholder" in report["errors"]
    assert "release_tag_invalid_or_placeholder" in report["errors"]


def test_release_preflight_requires_private_dedicated_rclone_directory(tmp_path: Path):
    release_environment, _ = fixture(tmp_path)
    release_values = preflight.read_environment(release_environment)
    rclone_directory = Path(release_values["HERMES_RCLONE_CONFIG_DIR"])
    rclone_directory.chmod(0o755)

    report = preflight.collect(release_environment)

    assert f"sensitive_directory_permissions_too_open:{rclone_directory}" in report["errors"]

    rclone_directory.chmod(0o700)
    outside = rclone_directory.parent / "outside-rclone.conf"
    write(outside, "[controlled]\ntype = local\n")
    release_text = release_environment.read_text(encoding="utf-8").replace(
        str(rclone_directory / "rclone.conf"), str(outside)
    )
    write(release_environment, release_text)

    report = preflight.collect(release_environment)

    assert "rclone_config_file_not_in_dedicated_directory" in report["errors"]
