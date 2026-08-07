# open-dashboard-runbook

Use when Łukasz asks to open or expose the Hermes dashboard.

## Rule

Do not expose the dashboard publicly without:
- HTTPS,
- authentication,
- firewall restrictions,
- reverse proxy,
- explicit approval from Łukasz.

## Workflow

1. Identify current dashboard bind address and port.
2. Check Docker published ports or system service port.
3. Prefer private access first: SSH tunnel or VPN.
4. If public access is requested, prepare a plan for domain, DNS, reverse proxy, TLS, auth, firewall, and rollback.
5. Ask for explicit approval before applying public exposure changes.

## Safe private access example

```bash
ssh -L 127.0.0.1:8787:127.0.0.1:8787 hermesadmin@IP_SERWERA
```

Then open `http://127.0.0.1:8787` locally.

Adjust ports after checking the actual Hermes configuration.
