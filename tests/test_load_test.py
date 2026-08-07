from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

from hermes_load_test import run_load_test  # noqa: E402


def test_controlled_load_has_no_duplicates_or_final_offer_send(tmp_path: Path):
    report = run_load_test(event_count=50, workers=10, database=tmp_path / "load.sqlite3")

    assert report["status"] == "ok"
    assert report["operational_sent"] == 40
    assert report["final_offer_drafts_created"] == 10
    assert report["final_offer_sent"] == 0
    assert report["duplicate_replays_blocked"] == 50
    assert report["duplicate_external_sends"] == 0
    assert report["sqlite_lock_errors"] == 0
    assert report["queue_length_after_run"] == 0
    sent_types = {
        key.split(":", 1)[1]
        for key in report["outbox_by_status_type"]
        if key.startswith("sent:")
    }
    assert sent_types == {
        "acknowledgement",
        "clarification_request",
        "missing_data_request",
        "follow_up",
        "ready_for_offer_notice",
    }
