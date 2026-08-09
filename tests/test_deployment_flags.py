from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

from hermes_rfq_core import SafetySwitches
import llm_intent_classifier as lic


def test_safety_switches_defaults_match_spec_section_24():
    sw = SafetySwitches.from_env()
    # Spec section 24 recommended production defaults
    assert sw.tenant_required is True
    assert sw.llm_intent_enabled is False
    assert sw.llm_shadow_mode is True
    assert sw.auto_reply_low_risk is False
    assert sw.reply_kill_switch is False


def test_safety_switches_read_env_flags():
    env = {
        "HERMES_TENANT_REQUIRED": "0",
        "HERMES_LLM_INTENT_ENABLED": "1",
        "HERMES_LLM_SHADOW_MODE": "0",
        "HERMES_AUTO_REPLY_LOW_RISK": "1",
        "HERMES_REPLY_KILL_SWITCH": "1",
    }
    old = os.environ.copy()
    os.environ.update(env)
    try:
        sw = SafetySwitches.from_env()
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert sw.tenant_required is False
    assert sw.llm_intent_enabled is True
    assert sw.llm_shadow_mode is False
    assert sw.auto_reply_low_risk is True
    assert sw.reply_kill_switch is True


def test_auto_reply_allowed_requires_low_risk_and_kill_switch_off():
    assert SafetySwitches(auto_reply_low_risk=True, reply_kill_switch=False).auto_reply_allowed is True
    assert SafetySwitches(auto_reply_low_risk=True, reply_kill_switch=True).auto_reply_allowed is False
    assert SafetySwitches(auto_reply_low_risk=False, reply_kill_switch=False).auto_reply_allowed is False


def test_kill_switch_immediately_stops_auto_reply():
    env = {"HERMES_AUTO_REPLY_LOW_RISK": "1", "HERMES_REPLY_KILL_SWITCH": "1"}
    old = os.environ.copy()
    os.environ.update(env)
    try:
        sw = SafetySwitches.from_env()
    finally:
        os.environ.clear()
        os.environ.update(old)
    assert sw.auto_reply_allowed is False


def test_classifier_is_enabled_respects_spec_flag():
    old = os.environ.pop("HERMES_LLM_INTENT_ENABLED", None)
    try:
        os.environ["HERMES_LLM_INTENT_ENABLED"] = "1"
        assert lic.is_enabled() is True
        os.environ["HERMES_LLM_INTENT_ENABLED"] = "0"
        assert lic.is_enabled() is False
        os.environ.pop("HERMES_LLM_INTENT_ENABLED", None)
        assert lic.is_enabled() is False  # spec default off
    finally:
        if old is not None:
            os.environ["HERMES_LLM_INTENT_ENABLED"] = old


def test_reset_test_registry_aborts_without_test_env(tmp_path: Path):
    reg = tmp_path / "reg.sqlite3"
    reg.write_text("dummy")
    env = {
        "HERMES_ENV": "production",
        "HERMES_REGISTRY_PATH": str(reg),
        "PATH": os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        [sys.executable, str(EXECUTION / "reset_test_registry.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    # The production registry file must be untouched.
    assert reg.exists()
    assert reg.read_text() == "dummy"


def test_reset_test_registry_removes_file_in_test_env(tmp_path: Path):
    reg = tmp_path / "reg.sqlite3"
    reg.write_text("dummy")
    env = {
        "HERMES_ENV": "test",
        "HERMES_REGISTRY_PATH": str(reg),
        "PATH": os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        [sys.executable, str(EXECUTION / "reset_test_registry.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert not reg.exists()


def test_reset_test_registry_idempotent_when_no_file(tmp_path: Path):
    reg = tmp_path / "absent.sqlite3"
    env = {
        "HERMES_ENV": "test",
        "HERMES_REGISTRY_PATH": str(reg),
        "PATH": os.environ.get("PATH", ""),
    }
    result = subprocess.run(
        [sys.executable, str(EXECUTION / "reset_test_registry.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_release_image_includes_mailbox_self_test_fixture():
    fixture = "tests/fixtures/mail-lead-pipeline/zoho_poll_sample.json"
    dockerfile = (ROOT / "infrastructure/docker/Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert f"COPY {fixture} " in dockerfile
    assert f"!{fixture}" in dockerignore


def test_every_release_image_stage_is_digest_pinned():
    dockerfile = (ROOT / "infrastructure/docker/Dockerfile").read_text(encoding="utf-8")
    from_instructions = [
        line.strip()
        for line in dockerfile.splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    pinned_from = re.compile(
        r"FROM\s+\S+@sha256:[0-9a-f]{64}(?:\s+AS\s+[A-Za-z0-9_.-]+)?",
        re.IGNORECASE,
    )

    assert from_instructions
    assert all(pinned_from.fullmatch(line) for line in from_instructions)


def test_base_security_override_keeps_hermes_dependency_metadata_consistent():
    dockerfile = (ROOT / "infrastructure/docker/Dockerfile").read_text(encoding="utf-8")
    overrides = (ROOT / "requirements-base-security.in").read_text(
        encoding="utf-8"
    )
    node_overrides = json.loads(
        (ROOT / "infrastructure/docker/node-security/package.json").read_text(
            encoding="utf-8"
        )
    )["dependencies"]

    assert "cryptography==50.0.0" in overrides
    assert "msal==1.37.0" in overrides
    assert node_overrides == {
        "brace-expansion": "5.0.9",
        "ip-address": "10.3.1",
        "nanoid": "3.3.17",
        "undici": "7.29.0",
    }
    assert 'old = "\\\"cryptography==48.0.1\\\""' in dockerfile
    assert '--editable /opt/hermes' in dockerfile
    assert "uv pip check --python /opt/hermes/.venv/bin/python" in dockerfile
    assert "npm ci --prefix /opt/hermes-node-security" in dockerfile
    assert "npm pkg set overrides.brace-expansion=5.0.9" in dockerfile
    assert "npm pkg set overrides.nanoid=3.3.17" in dockerfile
    assert "npm pkg set overrides.undici=7.29.0" in dockerfile


def test_release_runtime_forbids_token_billed_model_api():
    environment_template = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "infrastructure" / "docker" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert "OPENAI_API_KEY=" not in environment_template
    assert "ORCHESTA_RFQ_ALLOW_EXTERNAL_VISION=0" in environment_template
    assert "ORCHESTA_RFQ_ALLOW_SCHEMA_MODEL=0" in environment_template
    assert 'OPENAI_API_KEY: ""' in compose
    assert "HERMES_EXPECTED_SKILL_ROOT: /opt/hermes-release/app/skills" in compose
    assert 'ORCHESTA_RFQ_ALLOW_EXTERNAL_VISION: "0"' in compose
    assert 'ORCHESTA_RFQ_ALLOW_SCHEMA_MODEL: "0"' in compose
