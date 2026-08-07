#!/usr/bin/env python3
"""Export a reproducible, secret-free Hermes VPS snapshot into this repository."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any

import yaml


DATA_ROOT = "/docker/<SET_ME_COMPOSE_PROJECT>/data"
SKILLS_ROOT = "/root/HermesVault/Hermes/Skills"
DOCKER_ROOT = "/docker/<SET_ME_COMPOSE_PROJECT>"
CONTAINER = "<SET_ME_CONTAINER_NAME>"

SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])("
    r"api[_-]?key|token|secret|password|passphrase|credential|private[_-]?key|"
    r"session[_-]?key|password[_-]?hash|oauth|client[_-]?secret"
    r")(?:$|[_-])",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(r"https?://[^/\s:@]+:[^@\s/]+@"),
)


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


class Remote:
    def __init__(self, env: dict[str, str]) -> None:
        required = (
            "HOSTINGER_VPS_SSH_PRIVATE_KEY_PATH",
            "HOSTINGER_VPS_ADMIN_USER",
            "HOSTINGER_VPS_IP",
        )
        missing = [key for key in required if not env.get(key)]
        if missing:
            raise SystemExit(f"Missing SSH settings: {', '.join(missing)}")

        self.user = env["HOSTINGER_VPS_ADMIN_USER"]
        self.host = env["HOSTINGER_VPS_IP"]
        self.port = env.get("HOSTINGER_VPS_PORT", "22")
        self.ssh = [
            "ssh",
            "-i",
            env["HOSTINGER_VPS_SSH_PRIVATE_KEY_PATH"],
            "-p",
            self.port,
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "ConnectTimeout=10",
        ]

    @property
    def destination(self) -> str:
        return f"{self.user}@{self.host}"

    def text(self, command: str) -> str:
        result = run(self.ssh + [self.destination, command], capture=True)
        return result.stdout

    def read(self, path: str) -> str:
        return self.text(f"sudo -n cat -- {shlex.quote(path)}")

    def rsync(
        self,
        source: str,
        destination: Path,
        *,
        excludes: tuple[str, ...] = (),
    ) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        ssh_transport = shlex.join(self.ssh)
        command = [
            "rsync",
            "-rltp",
            "--safe-links",
            "--prune-empty-dirs",
            "--rsync-path=sudo -n rsync",
            "-e",
            ssh_transport,
        ]
        for pattern in excludes:
            command.extend(["--exclude", pattern])
        command.extend(
            [
                f"{self.destination}:{source.rstrip('/')}/",
                f"{destination}/",
            ]
        )
        run(command)


def contains_secret(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS)


def sensitive_key(key: str) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()
    return bool(SENSITIVE_KEY.search(normalized))


def sanitize_string(value: str) -> str:
    env_assignment = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", value, re.DOTALL)
    if env_assignment:
        key, assigned = env_assignment.groups()
        if sensitive_key(key) or contains_secret(assigned):
            return f"{key}=<SET_AFTER_RESTORE>"
    if contains_secret(value):
        return "<SET_AFTER_RESTORE>"
    return value


def sanitize(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {item_key: sanitize(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if sensitive_key(key):
        return "<SET_AFTER_RESTORE>"
    if isinstance(value, str):
        return sanitize_string(value)
    return value


def write_text(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def env_template(raw: str, source: str) -> str:
    output = [
        "# Secret-free template generated from the active VPS configuration.",
        f"# Source: {source}",
        "# Replace every <SET_AFTER_RESTORE> value locally. Never commit the completed file.",
        "",
    ]
    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if sensitive_key(key) or contains_secret(value):
            output.append(f"{key}=<SET_AFTER_RESTORE>")
        else:
            output.append(f"{key}={value}")
    return "\n".join(output) + "\n"


def sanitize_crontab(raw: str) -> str:
    result: list[str] = []
    assignment = re.compile(r"\b([A-Z][A-Z0-9_]*)=([^\s]+)")
    for line in raw.splitlines():
        def replace(match: re.Match[str]) -> str:
            key, value = match.groups()
            if sensitive_key(key) or contains_secret(value):
                return f"{key}=<SET_AFTER_RESTORE>"
            return match.group(0)

        result.append(assignment.sub(replace, line))
    return "\n".join(result) + "\n"


def export(output: Path, remote: Remote) -> None:
    if not (output / ".git").is_dir():
        raise SystemExit(f"Output is not a Git checkout: {output}")

    common_excludes = (
        ".git/",
        ".env",
        ".env.*",
        "._*",
        "__pycache__/",
        "*.pyc",
        "*.pyo",
        "*.bak",
        "*.bak-*",
        "*.bak.*",
        "*.lock",
        "*.sqlite*",
        "*.db",
        "*.db-*",
        "*token.json",
        "*credentials*",
        "*secret*",
        "*.pem",
        "*.key",
        "*.tar.gz",
    )

    trees = {
        "execution": (),
        "directives": (),
        "docs": ("raw_results.json",),
        "tests": (),
        "scripts": (),
        "hooks": (),
        "automations": ("runs/",),
    }
    for name, extra_excludes in trees.items():
        remote.rsync(
            f"{DATA_ROOT}/{name}",
            output / name,
            excludes=common_excludes + extra_excludes,
        )

    remote.rsync(
        SKILLS_ROOT,
        output / "skills",
        excludes=common_excludes
        + (
            ".curator_backups/",
            ".hub/",
            ".usage.json",
            ".usage.json.lock",
            ".curator_state",
            ".bundled_manifest",
        ),
    )

    for remote_path, local_path in (
        (f"{DATA_ROOT}/SOUL.md", output / "SOUL.md"),
        (f"{DATA_ROOT}/memories/MEMORY.md", output / "memories/MEMORY.md"),
        (f"{DATA_ROOT}/memories/USER.md", output / "memories/USER.md"),
    ):
        write_text(local_path, remote.read(remote_path))

    config = yaml.safe_load(remote.read(f"{DATA_ROOT}/config.yaml")) or {}
    write_text(
        output / "config.yaml",
        yaml.safe_dump(sanitize(config), sort_keys=False, allow_unicode=True, width=120),
    )
    write_text(
        output / ".env.example",
        env_template(remote.read(f"{DATA_ROOT}/.env"), f"{DATA_ROOT}/.env"),
    )

    jobs = json.loads(remote.read(f"{DATA_ROOT}/cron/jobs.json"))
    write_text(
        output / "cron/jobs.json",
        json.dumps(sanitize(jobs), ensure_ascii=False, indent=2) + "\n",
    )

    compose = yaml.safe_load(remote.read(f"{DOCKER_ROOT}/docker-compose.yml")) or {}
    sanitized_compose = sanitize(compose)
    write_text(
        output / "infrastructure/docker/docker-compose.yml",
        yaml.safe_dump(sanitized_compose, sort_keys=False, allow_unicode=True, width=120),
    )
    repo_digest = remote.text(
        "sudo -n docker image inspect ghcr.io/hostinger/hvps-hermes-agent:latest "
        "--format '{{index .RepoDigests 0}}'"
    ).strip()
    pinned_compose = copy.deepcopy(sanitized_compose)
    if repo_digest:
        for service in pinned_compose.get("services", {}).values():
            if isinstance(service, dict) and str(service.get("image", "")).startswith(
                "ghcr.io/hostinger/hvps-hermes-agent"
            ):
                service["image"] = repo_digest
    write_text(
        output / "infrastructure/docker/docker-compose.pinned.yml",
        yaml.safe_dump(pinned_compose, sort_keys=False, allow_unicode=True, width=120),
    )
    write_text(
        output / "infrastructure/docker/.env.example",
        env_template(remote.read(f"{DOCKER_ROOT}/.env"), f"{DOCKER_ROOT}/.env"),
    )

    unit_names = (
        "hermes-dashboard-private.service",
        "hermes-gateway.service",
        "hermes-vps-daily-audit.service",
        "hermes-vps-daily-audit.timer",
    )
    for name in unit_names:
        write_text(
            output / "infrastructure/systemd" / name,
            remote.read(f"/etc/systemd/system/{name}"),
        )

    for remote_path in (
        "/usr/local/bin/hermes-gateway-run.sh",
        "/usr/local/sbin/hermes-vps-daily-host-audit",
    ):
        write_text(
            output / "infrastructure/host-scripts" / Path(remote_path).name,
            remote.read(remote_path),
            0o755,
        )

    crontab = remote.text("sudo -n crontab -l 2>/dev/null || true")
    write_text(output / "infrastructure/cron/root.crontab", sanitize_crontab(crontab))

    ufw_raw = remote.text("sudo -n ufw status verbose 2>/dev/null || true")
    ufw = "\n".join(line.rstrip() for line in ufw_raw.splitlines()).rstrip() + "\n"
    write_text(output / "infrastructure/firewall/ufw-status.txt", ufw)

    sshd = remote.text(
        "sudo -n sshd -T 2>/dev/null | "
        "grep -E '^(port|permitrootlogin|passwordauthentication|kbdinteractiveauthentication|"
        "pubkeyauthentication|allowusers|allowgroups|maxauthtries|x11forwarding) ' || true"
    )
    write_text(output / "infrastructure/ssh/sshd-effective.txt", sshd)

    runtime = remote.text(
        "sudo -n docker inspect "
        + CONTAINER
        + " --format 'image={{.Config.Image}}\\nimage_id={{.Image}}\\nrestart={{.HostConfig.RestartPolicy.Name}}"
        + "\\nnetwork={{.HostConfig.NetworkMode}}' && "
        + "sudo -n docker image inspect ghcr.io/hostinger/hvps-hermes-agent:latest "
        + "--format 'repo_digest={{index .RepoDigests 0}}' && "
        + "sudo -n docker exec "
        + CONTAINER
        + " sh -lc 'python3 --version; hermes --version 2>/dev/null | head -5; "
        + "python3 -m pip show weasyprint jinja2 pypdf 2>/dev/null | grep -E \"^(Name|Version):\"'"
    )
    write_text(output / "inventory/runtime-versions.txt", runtime)

    excluded = """# Excluded VPS paths

These paths are intentionally not committed because they contain secrets, runtime state,
customer data, generated output, replaceable dependencies, or large caches.

- `/docker/<SET_ME_COMPOSE_PROJECT>/data/.env`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/auth.json`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/google_client_secret.json`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/google_token.json`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/.ssh/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/.tmp/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/.backups/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/CRM/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/logs/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/sessions/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/reports/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/rfq-state/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/rfq-runtime/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/cache/`, `.cache/`, `.local/`, `.npm/`
- `/docker/<SET_ME_COMPOSE_PROJECT>/data/lsp/node_modules/`
- Python virtual environments and generated cron output
- historical skill and code backup directories

The excluded runtime and dependency directories are regenerated by the restore procedure.
Production credentials must be supplied again after restore.
"""
    write_text(output / "inventory/excluded-paths.md", excluded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    env = parse_dotenv(args.env_file.resolve())
    output = args.output.resolve()
    export(output, Remote(env))
    print(f"export-hermes-vps-backup: snapshot prepared in {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
