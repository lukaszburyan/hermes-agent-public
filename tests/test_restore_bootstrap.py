from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESTORE = ROOT / "restore" / "restore-hermes.sh"


def test_restore_bootstrap_uses_immutable_release_and_never_copies_application_code():
    script = RESTORE.read_text(encoding="utf-8")

    assert 'TARGET="/docker/hermes-agent/runtime"' in script
    assert 'DOCKER_TARGET="/docker/hermes-agent/release"' in script
    assert '"$ROOT/infrastructure/docker/docker-compose.yml"' in script
    assert "docker-compose.pinned.yml" not in script
    assert "for directory in automations" not in script
    assert "cron/jobs.json" not in script
    assert "rsync" not in script
    assert "*.service" in script and "*.timer" in script
    assert "infrastructure/systemd/*;" not in script
    assert "hermes-release-preflight" in script
    assert "restore encrypted state" in script
