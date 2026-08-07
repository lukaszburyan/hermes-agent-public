from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import tenant_config


def test_orchesta_package_has_all_required_files():
    assert tenant_config.validate_tenant_package("orchesta") == []


def test_firma_abc_package_has_all_required_files():
    assert tenant_config.validate_tenant_package("firma_abc") == []


def test_orchesta_company_identity_lives_in_tenant_package():
    company = tenant_config.load_company("orchesta")
    assert company["company"]["name"] == "Orchesta"
    assert company["company"]["mailbox"] == "rfq-mailbox@example.invalid"
    assert company["seller"]["full_name"] == "Orchesta RFQ Team"


def test_firma_abc_company_identity_is_distinct_from_orchesta():
    company = tenant_config.load_company("firma_abc")
    assert company["company"]["name"] == "Firma ABC"
    assert company["company"]["mailbox"] == "identity-068@example.invalid"
    assert company["seller"]["full_name"] != "Orchesta RFQ Team"


def test_orchesta_notifications_use_telegram_firma_abc_uses_email():
    orch = tenant_config.load_notifications("orchesta")
    assert orch["internal_notifications"]["human_review"]["telegram"]["enabled"] is True
    abc = tenant_config.load_notifications("firma_abc")
    assert abc["internal_notifications"]["human_review"]["telegram"]["enabled"] is False
    assert abc["internal_notifications"]["human_review"]["email"]["enabled"] is True


def test_discovery_questions_are_tenant_specific():
    assert (
        tenant_config.discovery_questions("orchesta", "mailbox_count", "pl")
        == "Ile kont pocztowych ma śledzić system?"
    )
    assert (
        tenant_config.discovery_questions("orchesta", "mailbox_count", "en")
        == "How many inboxes should the system monitor?"
    )


def test_unknown_tenant_package_is_reported_missing():
    missing = tenant_config.validate_tenant_package("nonexistent_tenant")
    assert missing and "missing" in missing[0]


def test_orchesta_products_pricing_lives_in_tenant_package():
    products = tenant_config.load_products("orchesta")
    assert products["pricing_id"] == "orchesta-rfq-2026-07"
    assert products["base_offer"]["net_price"] == 7200
