# Hermes Agent

Public, sanitized source snapshot of Hermes Agent and its RFQ automation.

Current public snapshot: `v0.18.0-rc12` (sanitized from the production source
tree on 2026-08-09). Private deployment reports, runtime identifiers and
operational evidence are not part of this repository.

The repository contains the application code, tests, reusable skills, example
tenant configuration, Docker/Systemd templates, migration tooling and safety
controls. Operational reports, production endpoints, real contact addresses,
provider message identifiers, runtime state and credentials are intentionally
excluded.

## Safety model

- operational RFQ messages may use automatic transport only after durable
  recipient, tenant, thread and policy validation;
- final offers containing price, final scope, payment terms or an attached PDF
  are draft-only;
- credentials and environment-specific values must be supplied outside Git;
- example identities use reserved, non-routable domains.

## Local validation

```bash
python -m pytest -q \
  --ignore=tests/test_canonical_entrypoints.py \
  --ignore=tests/test_free_github_governance.py
gitleaks git . --redact --no-banner
```

Copy `.env.example` to a local, ignored environment file and replace only the
placeholder values needed for your environment. Never commit the completed
file.

## Provenance

This repository starts with a fresh root commit. It does not inherit the Git
history, release reports or deployment evidence of any private environment.
Subsequent updates are published as sanitized snapshot commits and never import
the private repository's Git history.
