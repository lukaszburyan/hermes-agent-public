# OAuth setup notes from headless Google Sheets integration

Use this when configuring Google Drive/Sheets OAuth from a headless Hermes session.

## Minimal scope for Sheets lead automation

For Google Sheets lead polling/editing, Drive + Sheets scopes are enough:

- `https://www.googleapis.com/auth/drive`
- `https://www.googleapis.com/auth/spreadsheets`

Avoid asking for Gmail/Calendar/Docs when the task is only Sheets.

## Redirect URI mismatch

If Google returns `redirect_uri_mismatch`, inspect the OAuth client and ensure the exact redirect URI used in the generated URL is registered. Google treats these as distinct:

- `http://localhost`
- `http://localhost/`
- `http://localhost:1`

For Web application clients, add the exact variant(s) under Authorized redirect URIs. For Desktop app clients, prefer the downloaded Desktop JSON, but still verify the actual redirect used by the local setup script.

## Headless flow pattern

1. Generate an auth URL and persist pending OAuth state + PKCE verifier.
2. User opens the URL and clicks through Google warnings/consent.
3. Browser may fail on localhost; this is expected.
4. User must paste back the full final URL containing `code=`.
5. Exchange that URL/code with the same pending state/verifier.

Intermediate Google URLs like `/signin/oauth/warning` or `/signin/oauth/v2/consentsummary` are not usable; keep guiding until the URL contains `code=`.

## PEP 668 / dependency install

If setup tries to install Google packages into an externally managed system Python and fails with PEP 668, create/use a project venv instead of using `--break-system-packages`, e.g.:

```bash
python3 -m venv /opt/data/.venvs/google-workspace
/opt/data/.venvs/google-workspace/bin/python -m pip install --upgrade pip
/opt/data/.venvs/google-workspace/bin/pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

Then run the setup/exchange scripts with that venv Python.

## Verification

After token exchange:

- `setup.py --check` should show authenticated, possibly partial if only Drive/Sheets were granted.
- Use Sheets API to read metadata and a small range.
- Verify `capabilities.canEdit` before attempting status updates.
