# security-audit

Use for weekly VPS security checks or before exposing any Hermes dashboard endpoint.

## Workflow

1. Run `execution/security-audit.sh`.
2. Check firewall.
3. Check SSH settings.
4. Check open ports.
5. Check sudo-capable users.
6. Check Docker published ports.
7. Check Hostinger backups in the dashboard/API.
8. Report risks before making changes.

## Hard stop

Require explicit `OK` before changing firewall rules, SSH settings, root login, sudo users, backup settings, reverse proxy, HTTPS, DNS, or public dashboard exposure.

## Output

Short Polish report:
- OK items,
- risks,
- exact proposed changes,
- commands to run after approval.
