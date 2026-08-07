FROM ghcr.io/astral-sh/uv@sha256:069a51314a7bb6031777a9273205fe1b0b19e914ef418207d1338b268df641dd AS uv

FROM ghcr.io/hostinger/hvps-hermes-agent@sha256:e155dfacd997b31d85506148d94527862138c351a74d1650ebc4a1a658b6cad5 AS node-security

COPY infrastructure/docker/node-security/package.json infrastructure/docker/node-security/package-lock.json /opt/hermes-node-security/
RUN npm ci --prefix /opt/hermes-node-security --ignore-scripts --no-audit --no-fund

FROM ghcr.io/hostinger/hvps-hermes-agent@sha256:e155dfacd997b31d85506148d94527862138c351a74d1650ebc4a1a658b6cad5

ARG HERMES_RELEASE_COMMIT=unknown
ARG HERMES_RELEASE_TAG=unreleased

LABEL org.opencontainers.image.source="https://github.com/lukaszburyan/hermes-agent-public" \
      org.opencontainers.image.revision="${HERMES_RELEASE_COMMIT}" \
      org.opencontainers.image.version="${HERMES_RELEASE_TAG}"

COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends age rclone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-base-security.lock /opt/hermes-release/requirements-base-security.lock
RUN /opt/hermes/.venv/bin/python -c 'from pathlib import Path; path = Path("/opt/hermes/pyproject.toml"); text = path.read_text(); old = "\"cryptography==48.0.1\""; assert text.count(old) == 1; path.write_text(text.replace(old, "\"cryptography==50.0.0\""))' \
    && uv pip install --python /opt/hermes/.venv/bin/python --no-deps --require-hashes \
      --exclude-newer 2026-08-07T00:00:00Z \
      -r /opt/hermes-release/requirements-base-security.lock \
    && uv pip install --python /opt/hermes/.venv/bin/python --reinstall --no-deps \
      --editable /opt/hermes \
    && uv pip check --python /opt/hermes/.venv/bin/python \
    && npm install --global --no-audit --no-fund npm@12.0.2

COPY --from=node-security /opt/hermes-node-security/node_modules/brace-expansion /usr/local/lib/node_modules/npm/node_modules/brace-expansion
COPY --from=node-security /opt/hermes-node-security/node_modules/ip-address /usr/local/lib/node_modules/npm/node_modules/ip-address
COPY --from=node-security /opt/hermes-node-security/node_modules/brace-expansion /opt/hermes/node_modules/brace-expansion
COPY --from=node-security /opt/hermes-node-security/node_modules/undici /opt/hermes/plugins/platforms/photon/sidecar/node_modules/undici

RUN npm pkg set overrides.brace-expansion=5.0.9 --prefix /opt/hermes \
    && npm pkg set overrides.undici=7.29.0 --prefix /opt/hermes/plugins/platforms/photon/sidecar \
    && node -e 'const expected={"/usr/local/lib/node_modules/npm/node_modules/brace-expansion/package.json":"5.0.9","/usr/local/lib/node_modules/npm/node_modules/ip-address/package.json":"10.3.1","/usr/local/lib/node_modules/npm/node_modules/tar/package.json":"7.5.19","/usr/local/lib/node_modules/npm/node_modules/undici/package.json":"6.27.0","/opt/hermes/node_modules/brace-expansion/package.json":"5.0.9","/opt/hermes/plugins/platforms/photon/sidecar/node_modules/undici/package.json":"7.29.0"}; for (const [path, version] of Object.entries(expected)) { if (require(path).version !== version) process.exit(1); }' \
    && npm --prefix /opt/hermes ls brace-expansion --all \
    && npm --prefix /opt/hermes/plugins/platforms/photon/sidecar ls undici --all

COPY requirements-release.lock /opt/hermes-release/requirements-release.lock
RUN /opt/hermes/.venv/bin/python -m venv /opt/hermes-release/.venv \
    && /opt/hermes-release/.venv/bin/python -m pip install --no-cache-dir --require-hashes \
       -r /opt/hermes-release/requirements-release.lock

COPY automations /opt/hermes-release/app/automations
COPY directives /opt/hermes-release/app/directives
COPY docs /opt/hermes-release/app/docs
COPY execution /opt/hermes-release/app/execution
COPY schemas /opt/hermes-release/app/schemas
COPY scripts /opt/hermes-release/app/scripts
COPY skills /opt/hermes-release/app/skills
COPY tenants /opt/hermes-release/app/tenants
COPY tests/fixtures/mail-lead-pipeline/zoho_poll_sample.json /opt/hermes-release/app/tests/fixtures/mail-lead-pipeline/zoho_poll_sample.json
COPY SOUL.md .env.example /opt/hermes-release/app/
COPY infrastructure/docker/release-entrypoint.sh /opt/hermes-release/release-entrypoint.sh

RUN chmod 0755 /opt/hermes-release/release-entrypoint.sh \
    /opt/hermes-release/app/scripts/*.sh \
    /opt/hermes-release/app/execution/*.sh \
    /opt/hermes-release/app/execution/hermes_release_monitor.py \
    /opt/hermes-release/app/execution/hermes_state_backup.py

ENV HERMES_APP_PYTHON=/opt/hermes-release/.venv/bin/python \
    HERMES_RFQ_VENV_PYTHON=/opt/hermes-release/.venv/bin/python \
    HERMES_RFQ_MARKER_ENABLED=0 \
    HERMES_RELEASE_COMMIT=${HERMES_RELEASE_COMMIT} \
    HERMES_RELEASE_TAG=${HERMES_RELEASE_TAG}

ENTRYPOINT ["/opt/hermes-release/release-entrypoint.sh"]
