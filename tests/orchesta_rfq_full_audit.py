#!/usr/bin/env python3
from __future__ import annotations

import base64
import importlib.util
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
EXEC = ROOT / 'execution'
if str(EXEC) not in sys.path:
    sys.path.insert(0, str(EXEC))
OUT = ROOT / 'docs' / 'orchesta-rfq-audit'
OUT.mkdir(parents=True, exist_ok=True)

FILES_TO_READ = [
    ROOT / 'skills/mail-lead-pipeline/SKILL.md',
    ROOT / 'skills/mail-lead-pipeline/references/classification.md',
    ROOT / 'skills/mail-lead-pipeline/references/business-profile.md',
    ROOT / 'skills/mail-lead-pipeline/references/draft-style.md',
    ROOT / 'skills/mail-lead-pipeline/references/attachment-processing.md',
    ROOT / 'skills/mail-lead-pipeline/references/research-and-crm.md',
    ROOT / 'skills/mail-lead-pipeline/references/telegram-escalation.md',
    ROOT / 'directives/mail-lead-pipeline.md',
    ROOT / 'execution/mail-lead-pipeline-dry-run.py',
    ROOT / 'execution/zoho_mail_poller.py',
    ROOT / 'execution/pipeline_state.py',
    ROOT / 'execution/zoho_reply_draft.py',
    ROOT / 'execution/attachment_router.py',
    ROOT / 'execution/rfq_attachment_extract.py',
    ROOT / 'skills/rfq-final-offer/SKILL.md',
    ROOT / 'skills/rfq-final-offer/references/workflow.md',
    ROOT / 'skills/rfq-final-offer/references/pdf-control.md',
    ROOT / 'skills/rfq-final-offer/references/obsidian-crm.md',
    ROOT / 'skills/rfq-final-offer/scripts/rfq_final_offer.py',
]
FILES_TO_READ += sorted((ROOT / 'skills/rfq-final-offer/rfq-final-offer-knowledge/approved').glob('*'))
FILES_TO_READ += sorted((ROOT / 'skills/rfq-final-offer/templates').glob('*'))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

poller = load_module('zoho_mail_poller_audit_full', EXEC / 'zoho_mail_poller.py')
state_mod = load_module('pipeline_state_audit_full', EXEC / 'pipeline_state.py')
classifier = poller.load_classifier()
router = load_module('attachment_router_audit_full', EXEC / 'attachment_router.py')


def timed(fn, *args, **kwargs):
    start = time.perf_counter()
    res = fn(*args, **kwargs)
    return res, time.perf_counter() - start


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = (len(xs) - 1) * p
    lo = math.floor(k); hi = math.ceil(k)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi-k) + xs[hi] * (k-lo)


def stats(values: list[float]) -> dict[str, float | int]:
    return {
        'n': len(values),
        'min_ms': round(min(values)*1000, 3) if values else 0,
        'median_ms': round(statistics.median(values)*1000, 3) if values else 0,
        'p90_ms': round(pct(values, 0.90)*1000, 3),
        'p95_ms': round(pct(values, 0.95)*1000, 3),
        'p99_ms': round(pct(values, 0.99)*1000, 3),
        'max_ms': round(max(values)*1000, 3) if values else 0,
    }


def make_message(case: dict[str, Any], idx: int) -> dict[str, Any]:
    msg = case['message']
    message_id = f"audit-{idx:04d}-{case['id']}"
    attachments = msg.get('attachments') or []
    return {
        'messageId': message_id,
        'folderId': 'inbox-1',
        'fromAddress': msg.get('from', f'audit{idx}@example.com'),
        'subject': msg.get('subject', ''),
        'hasAttachment': '1' if attachments else '0',
        '_content': '<p>' + str(msg.get('body', '')).replace('\n', '<br>') + '</p>',
        '_header': f"Message-ID: <{message_id}@audit.example>\nFrom: {msg.get('from', '')}\nSubject: {msg.get('subject', '')}\n",
        '_attachmentinfo': [
            {
                'attachmentId': f'att-{idx}-{j}',
                'attachmentName': a.get('filename', f'attachment-{j}.txt'),
                'contentType': a.get('mime') or a.get('contentType') or 'text/plain',
                'size': a.get('size') or len(str(a.get('content', 'synthetic'))),
                '_content': a.get('content', 'synthetic attachment'),
            }
            for j, a in enumerate(attachments)
        ],
        '_context': case.get('context', {}),
    }


def dataset_from_cases(cases: list[dict[str, Any]], n: int) -> dict[str, Any]:
    messages = [make_message(cases[i % len(cases)], i) for i in range(n)]
    return {
        'accounts': [{'accountId': 'acc-1', 'primaryEmailAddress': 'rfq-mailbox@example.invalid'}],
        'folders': [
            {'folderId': 'inbox-1', 'folderName': 'Inbox'},
            {'folderId': 'drafts-1', 'folderName': 'Drafts'},
        ],
        'messages': messages,
    }


def mock_draft_creator(*args, **kwargs):
    return {'action': 'created', 'status': 201, 'response': {'data': {'draftId': f'draft-{time.perf_counter_ns()}'}}}


def mock_final_offer_creator(*args, **kwargs):
    return {'action': 'awaiting_data_draft_created', 'status': 201, 'response': {'data': {'draftId': f'offer-{time.perf_counter_ns()}'}}, 'manifest': {'version': 'v1'}, 'telegram_text': 'synthetic'}


def run_poller_series(cases: list[dict[str, Any]], n: int, auto: bool = False) -> dict[str, Any]:
    data = dataset_from_cases(cases, n)
    client = poller.FakeZohoClient(data)
    with tempfile.TemporaryDirectory(prefix='rfq_audit_state_') as tmp:
        state = state_mod.PipelineState(Path(tmp) / 'state.sqlite3')
        summary, elapsed = timed(
            poller.poll,
            client, state, classifier,
            auto_draft=auto,
            draft_creator=mock_draft_creator if auto else None,
            auto_final_offer=auto,
            final_offer_creator=mock_final_offer_creator if auto else None,
            auto_draft_classes={'new_quote_request'},
            limit=n,
            run_id=f'audit-{n}-{auto}',
        )
        state.save()
        summary2, elapsed2 = timed(poller.poll, client, state, classifier, limit=n, run_id=f'audit-{n}-repeat')
    return {
        'n': n,
        'auto_draft_mock': auto,
        'elapsed_ms': round(elapsed*1000, 3),
        'per_message_ms': round(elapsed*1000/max(n,1), 3),
        'repeat_elapsed_ms': round(elapsed2*1000, 3),
        'summary': {k: summary.get(k) for k in ['listed','already_processed','precheck_skipped','woken','would_create','drafts_created','final_offer_attempted','final_offer_drafts_created','draft_duplicates_blocked','telegram_errors']},
        'repeat_summary': {k: summary2.get(k) for k in ['listed','already_processed','precheck_skipped','woken','would_create','drafts_created']},
        'fetch_counts': client.fetch_counts,
    }


def run_classifier_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    timings = []
    mismatches = []
    for case in cases:
        result, elapsed = timed(classifier.classify, case)
        timings.append(elapsed)
        exp = case.get('expected_classification')
        if exp and result.get('classification') != exp:
            mismatches.append({'id': case['id'], 'expected': exp, 'got': result.get('classification')})
    return {'timing': stats(timings), 'mismatches': mismatches, 'accuracy': round((len(cases)-len(mismatches))/len(cases), 6)}


def run_attachment_tests() -> dict[str, Any]:
    cases = []
    with tempfile.TemporaryDirectory(prefix='rfq_attach_') as tmp:
        tmp = Path(tmp)
        files = {
            'safe_pdf': ('brief.pdf', b'%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF'),
            'exe_wrong_ext': ('brief.pdf', b'MZ' + b'\x00'*200),
            'macro_doc': ('quote.xlsm', b'PK\x03\x04' + b'\x00'*200),
            'big_file': ('big.pdf', b'%PDF-1.4\n' + b'0'*(11*1024*1024)),
            'path_traversal': ('../../evil.pdf', b'%PDF-1.4\n%%EOF'),
        }
        timings = []
        for label, (name, content) in files.items():
            path = tmp / Path(name).name
            path.write_bytes(content)
            route, elapsed = timed(router.route_attachment, {'filename': name, 'size_bytes': len(content)}, path=path, subject='Zapytanie ofertowe', body='Proszę o wycenę')
            timings.append(elapsed)
            cases.append({'label': label, 'filename': name, 'saved_name': path.name, 'route': route})
    return {'cases': cases, 'timing': stats(timings)}


def complete_offer_input() -> dict[str, Any]:
    return {
        'offer_number': 'ORCH-RFQ-2026-AUDIT', 'date': '2026-07-27', 'sequence': 777, 'account_email': 'rfq-mailbox@example.invalid', 'language': 'pl',
        'client': {'first_name': 'Tomasz', 'last_name': 'Nowak', 'full_name': 'Tomasz Nowak', 'company': 'Audit Test Sp. z o.o.', 'email': 'tomasz.nowak@example.com'},
        'scope': {'mailbox_count': 3, 'crm': True, 'inquiry_source': 'mail', 'has_sample_requests': True},
        'thread': {'thread_id': 'thread-audit', 'source_message_id': '<audit@example.com>'},
        'safety': {'thread_headers_valid': True, 'sender_matches_thread': True, 'attachments_safe': True, 'prompt_injection_detected': False, 'classification_confidence': 'high'},
        'customer_expectations': {'expects_sms': False, 'expects_full_automatic_technical_pricing': False, 'expects_auto_send_final_offers': False},
    }


def run_final_offer_tests() -> dict[str, Any]:
    script = ROOT / 'skills/rfq-final-offer/scripts/rfq_final_offer.py'
    results = []
    timings_pdf = []
    timings_html_block = []
    for i in range(3):
        with tempfile.TemporaryDirectory(prefix='rfq_final_pdf_') as tmp:
            tmp = Path(tmp); inp = tmp/'input.json'; out = tmp/'out'
            inp.write_text(json.dumps(complete_offer_input(), ensure_ascii=False), encoding='utf-8')
            proc, elapsed = timed(subprocess.run, [sys.executable, str(script), '--input', str(inp), '--output-dir', str(out), '--render-pdf'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            timings_pdf.append(elapsed)
            results.append({'mode': 'pdf', 'returncode': proc.returncode, 'manifest': json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip().startswith('{') else {}, 'stderr': proc.stderr[-300:]})
    with tempfile.TemporaryDirectory(prefix='rfq_final_nopdf_') as tmp:
        tmp = Path(tmp); inp = tmp/'input.json'; out = tmp/'out'
        inp.write_text(json.dumps(complete_offer_input(), ensure_ascii=False), encoding='utf-8')
        proc, elapsed = timed(subprocess.run, [sys.executable, str(script), '--input', str(inp), '--output-dir', str(out)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        timings_html_block.append(elapsed)
        results.append({'mode': 'no_pdf', 'returncode': proc.returncode, 'manifest': json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip().startswith('{') else {}, 'stderr': proc.stderr[-300:]})
    return {'results': results, 'pdf_timing': stats(timings_pdf), 'no_pdf_block_timing': stats(timings_html_block)}


def scan_files() -> dict[str, Any]:
    inventory = []
    secret_hits = []
    for p in FILES_TO_READ:
        if not p.exists() or p.name.startswith('._') or p.is_dir():
            continue
        raw = p.read_text(encoding='utf-8', errors='replace')
        inventory.append({'path': str(p.relative_to(ROOT)), 'bytes': p.stat().st_size, 'lines': raw.count('\n')+1, 'sha256_16': __import__('hashlib').sha256(raw.encode()).hexdigest()[:16]})
        for m in re.finditer(r'(?i)(access_token|refresh_token|client_secret|api_key|authorization|bearer)\s*[:=]\s*([A-Za-z0-9_.-]{12,})', raw):
            secret_hits.append({'path': str(p.relative_to(ROOT)), 'match': m.group(1), 'line': raw[:m.start()].count('\n')+1})
    return {'inventory': inventory, 'secret_pattern_hits': secret_hits}


def main():
    cases = json.loads((ROOT / 'tests/fixtures/mail-lead-pipeline/synthetic_audit_144_cases.json').read_text())
    scan = scan_files()
    classification = run_classifier_metrics(cases)
    series = [run_poller_series(cases, n, auto=False) for n in [1,10,50,100,500]]
    series_auto = [run_poller_series(cases, n, auto=True) for n in [10,50,100]]
    attachments = run_attachment_tests()
    final_offer = run_final_offer_tests()
    result = {
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'production_safety': {'zoho_live': False, 'oauth': False, 'cron_run': False, 'crm_write': False, 'note': 'offline/mock/synthetic only'},
        'scan': scan,
        'classification': classification,
        'poller_series': series,
        'poller_series_auto_mock': series_auto,
        'attachments': attachments,
        'final_offer': final_offer,
    }
    (OUT / 'raw_results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'out': str(OUT / 'raw_results.json'),
        'files_read': len(scan['inventory']),
        'secret_hits': len(scan['secret_pattern_hits']),
        'classification_accuracy': classification['accuracy'],
        'classification_mismatches': len(classification['mismatches']),
        'series': [{'n': x['n'], 'elapsed_ms': x['elapsed_ms'], 'repeat_already_processed': x['repeat_summary']['already_processed']} for x in series],
        'final_offer_pdf_statuses': [r['manifest'].get('status') for r in final_offer['results']],
    }, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
