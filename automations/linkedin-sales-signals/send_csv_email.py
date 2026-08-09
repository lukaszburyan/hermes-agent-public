#!/usr/bin/env python3
"""Send a CSV report by email using available environment credentials.
Preferred order: Resend API, SMTP, Zoho Mail API.
Loads /opt/data/.env if present. Never prints secrets.
"""
import argparse, base64, html, json, mimetypes, os, smtplib, ssl, sys, urllib.parse, urllib.request, urllib.error
from email.message import EmailMessage
from pathlib import Path


def load_dotenv(path='/opt/data/.env'):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
        s = line.strip()
        if not s or s.startswith('#') or '=' not in s:
            continue
        k, v = s.split('=', 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def send_resend(to, subject, body, attachment):
    key = os.getenv('RESEND_API_KEY')
    if not key:
        raise RuntimeError('RESEND_API_KEY missing')
    sender = os.getenv('RESEND_FROM') or os.getenv('EMAIL_FROM') or 'Hermes <identity-001@customer-001.example.com>'
    p = Path(attachment)
    payload = {
        'from': sender,
        'to': [to],
        'subject': subject,
        'text': body,
        'attachments': [{'filename': p.name, 'content': base64.b64encode(p.read_bytes()).decode('ascii')}],
    }
    req = urllib.request.Request('https://api.resend.com/emails', data=json.dumps(payload).encode('utf-8'), headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read().decode('utf-8', 'replace')
            return {'provider': 'resend', 'status': resp.status, 'response': data[:500]}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'Resend HTTP {e.code}: {e.read().decode("utf-8", "replace")[:1000]}')


def send_smtp(to, subject, body, attachment):
    host, user, password = os.getenv('SMTP_HOST'), os.getenv('SMTP_USER'), os.getenv('SMTP_PASSWORD')
    port = int(os.getenv('SMTP_PORT') or '587')
    if not (host and user and password):
        raise RuntimeError('SMTP_HOST/SMTP_USER/SMTP_PASSWORD missing')
    sender = os.getenv('SMTP_FROM') or os.getenv('EMAIL_FROM') or user
    p = Path(attachment)
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = sender, to, subject
    msg.set_content(body)
    ctype, _ = mimetypes.guess_type(str(p))
    maintype, subtype = (ctype or 'text/csv').split('/', 1)
    msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=60) as s:
        s.starttls(context=ctx)
        s.login(user, password)
        s.send_message(msg)
    return {'provider': 'smtp', 'status': 'accepted'}


def zoho_refresh_token():
    cid = os.getenv('ZOHO_MAIL_CLIENT_ID')
    secret = os.getenv('ZOHO_MAIL_CLIENT_SECRET')
    refresh = os.getenv('ZOHO_MAIL_REFRESH_TOKEN')
    base = (os.getenv('ZOHO_MAIL_ACCOUNTS_BASE_URL') or 'https://accounts.zoho.eu').rstrip('/')
    if not (cid and secret and refresh):
        raise RuntimeError('ZOHO_MAIL_CLIENT_ID/SECRET/REFRESH_TOKEN missing')
    data = urllib.parse.urlencode({'refresh_token': refresh, 'client_id': cid, 'client_secret': secret, 'grant_type': 'refresh_token'}).encode()
    req = urllib.request.Request(f'{base}/oauth/v2/token', data=data, method='POST')
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read().decode())
    token = out.get('access_token')
    if not token:
        raise RuntimeError('Zoho refresh did not return access_token')
    return token


def zoho_api(token, method, url, payload=None, content_type='application/json'):
    data = None
    headers = {'Authorization': f'Zoho-oauthtoken {token}', 'Accept': 'application/json'}
    if payload is not None:
        if content_type == 'application/json':
            data = json.dumps(payload).encode('utf-8')
        else:
            data = payload
        headers['Content-Type'] = content_type
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode('utf-8', 'replace')
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'replace')
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {'error': raw[:1000]}
        raise RuntimeError(f'Zoho HTTP {e.code}: {parsed}')


def send_zoho(to, subject, body, attachment):
    token = zoho_refresh_token()
    api_base = (os.getenv('ZOHO_MAIL_API_BASE_URL') or 'https://mail.zoho.eu/api').rstrip('/')
    if not api_base.endswith('/api'):
        api_base += '/api'
    account_email = os.getenv('ZOHO_MAIL_ACCOUNT_EMAIL') or os.getenv('EMAIL_FROM')
    status, accounts = zoho_api(token, 'GET', f'{api_base}/accounts')
    data = accounts.get('data') or []
    chosen = None
    for acc in data:
        emails = [str(acc.get(k) or '').lower() for k in ('mailboxAddress','primaryEmailAddress','emailAddress','accountDisplayName')]
        if account_email and account_email.lower() in emails:
            chosen = acc; break
    if chosen is None and data:
        chosen = data[0]
    if not chosen:
        raise RuntimeError('Zoho no mail account found')
    account_id = str(chosen.get('accountId') or chosen.get('accountID') or chosen.get('id') or '')
    from_addr = account_email or chosen.get('primaryEmailAddress') or chosen.get('mailboxAddress')
    if not account_id or not from_addr:
        raise RuntimeError('Zoho account_id/fromAddress missing')
    p = Path(attachment)
    q = urllib.parse.urlencode({'fileName': p.name, 'isInline': 'false'})
    st, att = zoho_api(token, 'POST', f'{api_base}/accounts/{account_id}/messages/attachments?{q}', payload=p.read_bytes(), content_type='application/octet-stream')
    att_data = att.get('data') or att
    if isinstance(att_data, list):
        att_rec = att_data[0]
    else:
        att_rec = att_data
    attach_payload = {k: att_rec.get(k) for k in ('storeName','attachmentName','attachmentPath') if att_rec.get(k)}
    if len(attach_payload) < 3:
        raise RuntimeError(f'Zoho attachment upload missing fields: {att}')
    content = '<br>'.join(html.escape(body).splitlines())
    payload = {'fromAddress': from_addr, 'toAddress': to, 'subject': subject, 'content': content, 'mailFormat': 'html', 'attachments': [attach_payload]}
    st, sent = zoho_api(token, 'POST', f'{api_base}/accounts/{account_id}/messages', payload=payload)
    return {'provider': 'zoho', 'status': st, 'response': str(sent)[:500]}


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument('--to', required=True)
    ap.add_argument('--subject', required=True)
    ap.add_argument('--body', required=True)
    ap.add_argument('--attachment', required=True)
    args = ap.parse_args()
    errors = []
    for fn in (send_resend, send_smtp, send_zoho):
        try:
            result = fn(args.to, args.subject, args.body, args.attachment)
            print(json.dumps({'ok': True, **result}, ensure_ascii=False))
            return 0
        except Exception as e:
            errors.append(str(e))
    print(json.dumps({'ok': False, 'errors': errors}, ensure_ascii=False), file=sys.stderr)
    return 2

if __name__ == '__main__':
    raise SystemExit(main())
