from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import tenant_config


def test_orchesta_offer_yaml_is_single_source_of_required_fields():
    fields = tenant_config.offer_fields("orchesta")
    assert "mailbox_count" in fields
    assert "monthly_volume" in fields
    assert "company_name_or_website" in fields
    assert "current_process" in fields
    assert "inquiry_channels" in fields


def test_required_fields_for_final_offer_stage():
    final_offer = tenant_config.required_fields_for_stage("orchesta", "final_offer")
    assert "mailbox_count" in final_offer
    assert "monthly_volume" in final_offer
    assert "company_name_or_website" in final_offer
    # qualification-only fields are not required for final_offer
    assert "current_process" not in final_offer
    assert "inquiry_channels" not in final_offer


def test_required_fields_for_qualification_stage():
    qualification = tenant_config.required_fields_for_stage("orchesta", "qualification")
    assert "company_name_or_website" in qualification
    assert "current_process" in qualification
    assert "inquiry_channels" in qualification
    # final-offer-only fields are not required for qualification
    assert "monthly_volume" not in qualification
    assert "mailbox_count" not in qualification


def test_unknown_tenant_returns_empty():
    assert tenant_config.offer_fields("nonexistent_tenant") == {}
    assert tenant_config.required_fields_for_stage("nonexistent_tenant", "final_offer") == []
    assert tenant_config.load_tenant("nonexistent_tenant") == {}


def test_unknown_stage_returns_empty():
    assert tenant_config.required_fields_for_stage("orchesta", "bogus_stage") == []


def test_orchesta_internal_notification_recipient_is_the_approved_owner_mailbox():
    assert tenant_config.internal_notification_email("orchesta") == "identity-004@gmail.com"


def test_customer_visible_fields_for_final_offer():
    visible = tenant_config.customer_visible_fields("orchesta", "final_offer")
    assert "mailbox_count" in visible
    assert "monthly_volume" in visible
