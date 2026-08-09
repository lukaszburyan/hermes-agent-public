from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

from pipeline_state import PipelineState  # noqa: E402
from zoho_mail_poller import FakeZohoClient, load_classifier, poll  # noqa: E402
from tenant_config import resolve_tenant_id  # noqa: E402


def dataset() -> dict:
    return {
        "accounts": [{"accountId": "acc-1", "primaryEmailAddress": "rfq-mailbox@example.invalid"}],
        "folders": [
            {"folderId": "inbox-1", "folderName": "Inbox", "folderType": "Inbox"},
            {"folderId": "drafts-1", "folderName": "Drafts", "folderType": "Drafts"},
            {"folderId": "sent-1", "folderName": "Sent", "folderType": "Sent"},
        ],
        "messages": [],
    }


def test_resolve_tenant_id_does_not_default_to_orchesta_for_unknown_mailbox():
    # Spec section 2: unknown mailbox must NOT fall back to the Orchesta profile.
    assert resolve_tenant_id("random@unmapped.test") == ""
    assert resolve_tenant_id("rfq-mailbox@example.invalid") == "orchesta"
    assert resolve_tenant_id("identity-004@customer-004.example.com") == "orchesta"


def test_known_mailbox_resolves_to_orchesta(tmp_path: Path):
    summary = poll(
        FakeZohoClient(dataset()),
        PipelineState(tmp_path / "state.sqlite3"),
        load_classifier(),
        target_email="rfq-mailbox@example.invalid",
        run_id="test-tenant-known",
    )
    assert summary["tenant_id"] == "orchesta"
    assert summary.get("state") != "awaiting_human"


def test_unknown_mailbox_routes_to_awaiting_human(tmp_path: Path):
    with pytest.raises(RuntimeError, match="zoho_account_resolution_failed"):
        poll(
            FakeZohoClient(dataset()),
            PipelineState(tmp_path / "state.sqlite3"),
            load_classifier(),
            target_email="unknown@nowhere.test",
            run_id="test-tenant-unknown",
        )
