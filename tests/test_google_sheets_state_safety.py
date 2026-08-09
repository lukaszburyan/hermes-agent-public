from __future__ import annotations

import errno
import json
import multiprocessing
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import google_sheets_lead_poller as sheets  # noqa: E402


def _save_worker(args: tuple[str, int]) -> None:
    path, value = args
    sheets.save_state(path, {"rows": {str(value): {"hash": str(value)}}})


def _process_context():
    methods = multiprocessing.get_all_start_methods()
    return multiprocessing.get_context("fork" if "fork" in methods else "spawn")


def test_atomic_save_keeps_current_and_previous_valid_json(tmp_path: Path):
    path = tmp_path / "sheets-state.json"
    first = {"rows": {"2": {"hash": "first"}}}
    second = {"rows": {"2": {"hash": "second"}}}
    sheets.save_state(str(path), first)
    sheets.save_state(str(path), second)
    assert sheets.load_state(str(path)) == second
    assert json.loads((tmp_path / "sheets-state.json.previous").read_text(encoding="utf-8")) == first
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_corrupt_primary_is_quarantined_and_previous_state_is_recovered(tmp_path: Path):
    path = tmp_path / "sheets-state.json"
    first = {"rows": {"2": {"hash": "first"}}}
    sheets.save_state(str(path), first)
    sheets.save_state(str(path), {"rows": {"2": {"hash": "second"}}})
    path.write_text('{"rows": {"2": ', encoding="utf-8")
    recovered = sheets.load_state(str(path))
    alert = recovered.pop("_hermes_state_alert")
    assert recovered == first
    assert alert["reason"] == "primary_state_corrupt_recovered_from_previous"
    assert list(tmp_path.glob("sheets-state.json.corrupt-*"))
    assert not path.exists()


def test_corrupt_state_without_backup_stops_automatic_processing(tmp_path: Path):
    path = tmp_path / "sheets-state.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="corrupt_without_safe_backup"):
        sheets.load_state(str(path))
    assert list(tmp_path.glob("sheets-state.json.corrupt-*"))


def test_no_space_during_atomic_replace_preserves_last_good_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / "sheets-state.json"
    first = {"rows": {"2": {"hash": "first"}}}
    sheets.save_state(str(path), first)
    real_replace = os.replace

    def fail_final_replace(source, destination):
        if Path(destination) == path:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_replace(source, destination)

    monkeypatch.setattr(sheets.os, "replace", fail_final_replace)
    with pytest.raises(OSError) as error:
        sheets.save_state(str(path), {"rows": {"2": {"hash": "second"}}})
    assert error.value.errno == errno.ENOSPC
    assert sheets.load_state(str(path)) == first
    assert list(tmp_path.glob(".sheets-state.json.*.failed"))


def test_parallel_state_writes_leave_valid_current_and_previous_files(tmp_path: Path):
    path = tmp_path / "sheets-state.json"
    ctx = _process_context()
    with ctx.Pool(processes=4) as pool:
        pool.map(_save_worker, [(str(path), value) for value in range(8)])
    current = sheets.load_state(str(path))
    previous = json.loads((tmp_path / "sheets-state.json.previous").read_text(encoding="utf-8"))
    assert isinstance(current["rows"], dict) and len(current["rows"]) == 1
    assert isinstance(previous["rows"], dict) and len(previous["rows"]) == 1


def test_sheet_outcome_unknown_reconciles_only_by_exact_sent_marker(monkeypatch: pytest.MonkeyPatch):
    marker = "response:sheet:test:2"

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def list_accounts(self):
            return [{"accountId": "acc-1", "primaryEmailAddress": "rfq@example.com"}]

        def list_folders(self, _account_id):
            return [{"folderId": "sent-1", "folderName": "Sent", "folderType": "Sent"}]

        def list_messages(self, _account_id, _folder_id, _limit):
            return [{
                "messageId": "sent-sheet-1",
                "folderId": "sent-1",
                "toAddress": "client@example.com",
                "subject": "Dziękuję za zgłoszenie dotyczące Orchesta RFQ",
                "receivedTime": "1800000000000",
            }]

        def get_content(self, *_args):
            return f"Dziękuję<!-- hermes-send-marker:{marker} -->"

    monkeypatch.setattr(sheets, "HttpZohoClient", FakeClient)
    monkeypatch.setenv("ZOHO_MAIL_ACCOUNT_EMAIL", "rfq@example.com")
    result = sheets.reconcile_sheet_send(
        lead={"Email": "client@example.com"},
        operation={
            "operation_id": marker,
            "marker": marker,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        zoho_token_file="unused",
        env_file="unused",
    )
    assert result["resolved"] is True
    assert result["external_message_id"] == "sent-sheet-1"
