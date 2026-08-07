#!/usr/bin/env python3
"""Zoho Mail OAuth token exchanger (Guardrail 4-safe: no token-billed LLM API).

One-off helper to convert a Zoho Mail grant token (code) into a long-lived
refresh_token, then write the token file the poller / notify / pre-offer send
modules read. Run once after the user generates a grant token in the Zoho API
Console (https://api-console.zoho.eu).

Usage:
  python3 zoho_oauth_exchange.py \
      --client-id 1000.XXXXXXXXX \
      --client-secret xxxxxxxxxxxxxxxxxxxxxxxx \
      --code 1000.xxxxxxxxxxxxxxxx \
      --accounts-base https://accounts.zoho.eu \
      --api-base https://mail.zoho.eu/api \
      --token-file /opt/data/.tmp/zoho_mail_tokens.json

After this succeeds, also set in /opt/data/.env:
  ZOHO_MAIL_CLIENT_ID=<same client id>
  ZOHO_MAIL_CLIENT_SECRET=<same secret>
  ZOHO_MAIL_REFRESH_TOKEN=<printed refresh_token>
  ZOHO_MAIL_ACCOUNTS_BASE_URL=https://accounts.zoho.eu
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _post_form(url: str, fields: dict[str, str]) -> tuple[int, dict]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw[:500]}


def _get_json(url: str, access_token: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Authorization": f"Zoho-oauthtoken {access_token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw[:500]}


def main() -> int:
    p = argparse.ArgumentParser(description="Exchange a Zoho Mail grant token for a refresh_token")
    p.add_argument("--client-id", required=True)
    p.add_argument("--client-secret", required=True)
    p.add_argument("--code", required=True, help="Grant token from Zoho API Console")
    p.add_argument("--redirect-uri", default="", help="Must match the one used to generate the code (Self Client: leave empty or use the value shown)")
    p.add_argument("--accounts-base", default="https://accounts.zoho.eu")
    p.add_argument("--api-base", default="https://mail.zoho.eu/api")
    p.add_argument("--token-file", default="/opt/data/.tmp/zoho_mail_tokens.json")
    args = p.parse_args()

    fields = {
        "grant_type": "authorization_code",
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "code": args.code,
    }
    if args.redirect_uri:
        fields["redirect_uri"] = args.redirect_uri

    status, body = _post_form(f"{args.accounts_base}/oauth/v2/token", fields)
    if status != 200 or "refresh_token" not in body:
        print(f"EXCHANGE FAILED (HTTP {status}): {json.dumps(body, ensure_ascii=False)}", file=sys.stderr)
        return 2

    refresh_token = body["refresh_token"]
    access_token = body.get("access_token", "")
    print(f"refresh_token={refresh_token}")
    print(f"access_token={access_token} (short-lived; refresh_token is what matters)")

    account_id = ""
    if access_token:
        st, acc = _get_json(f"{args.api_base}/accounts", access_token)
        if st == 200:
            accounts = acc.get("data", {}).get("accounts", []) or acc.get("data", []) or []
            if accounts:
                account_id = str(accounts[0].get("accountId") or accounts[0].get("account_id") or "")
                print(f"account_id={account_id} (primary: {accounts[0].get('emailAddress','')})")

    token_data = {
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "accounts_base_url": args.accounts_base,
        "api_base": args.api_base,
        "account_id": account_id,
        "token": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": body.get("expires_in"),
            "api_domain": body.get("api_domain"),
        },
    }
    Path(args.token_file).parent.mkdir(parents=True, exist_ok=True)
    Path(args.token_file).write_text(json.dumps(token_data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import os as _os
        _os.chmod(args.token_file, 0o600)
    except OSError:
        pass
    print(f"token file written: {args.token_file}")
    print("\nNow add to /opt/data/.env:")
    print(f"ZOHO_MAIL_CLIENT_ID={args.client_id}")
    print(f"ZOHO_MAIL_CLIENT_SECRET={args.client_secret}")
    print(f"ZOHO_MAIL_REFRESH_TOKEN={refresh_token}")
    print(f"ZOHO_MAIL_ACCOUNT_ID={account_id}")
    print("ZOHO_MAIL_ACCOUNTS_BASE_URL=https://accounts.zoho.eu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
