from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPT = ROOT / "skills" / "rfq-final-offer" / "scripts" / "rfq_final_offer.py"
SKILL_DIR = SKILL_SCRIPT.parent.parent
if str(SKILL_SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPT.parent))

import importlib

rfo = importlib.import_module("rfq_final_offer")


def _complete_context() -> dict:
    return {
        "offer": {
            "offer_number": "ORCH-RFQ-2026-0001",
            "version": 1,
            "date": "2026-08-03",
        },
        "client": {
            "first_name": "Krzysztof",
            "last_name": "Nowak",
            "full_name": "Krzysztof Nowak",
            "company": "ABC Sp. z o.o.",
            "email": "identity-154@example.invalid",
        },
        "scope": {
            "mailbox_count": 3,
            "mailbox_label": "konta pocztowe",
            "crm_label": "tak",
            "inquiry_source_label": "mail",
        },
        "pricing": {
            "line_items": [
                {"name": "Orchesta RFQ, wdrożenie na 1 konto pocztowe", "net_display": "7200 zł"},
                {"name": "Każde kolejne konto pocztowe (2 szt.)", "net_display": "6000 zł"},
                {"name": "Integracja z CRM", "net_display": "3000 zł"},
            ],
            "net_total_display": "16200 zł",
        },
        "roi": {
            "title": "Oszczędność czasu",
            "description": "Krótszy czas reakcji to więcej szans na kontakt.",
            "hours_text": "Około 20 godzin oszczędności miesięcznie.",
            "cost_text": "Około 1100 zł oszczędności miesięcznie.",
        },
        "footer": "Orchesta RFQ Team\n📞 +48 000 000 000",
    }


def test_tenant_template_is_resolved_for_orchesta():
    template_dir, template_name, css_path = rfo.resolve_offer_template("orchesta")
    assert template_dir.name == "templates"
    assert "orchesta" in str(template_dir)
    assert template_name == "orchesta_offer.html.j2"
    assert (template_dir / template_name).exists()


def test_unknown_tenant_falls_back_to_skill_template():
    template_dir, template_name, _ = rfo.resolve_offer_template("nonexistent_tenant")
    assert template_dir == rfo.TEMPLATE_DIR
    assert template_name == "orchesta_offer.html.j2"


def test_rendered_orchesta_offer_html_has_no_telegram():
    html = rfo.render_offer_html_for_tenant(_complete_context(), tenant_id="orchesta")
    assert "Telegram" not in html
    assert "telegram" not in html.lower()
    # Sanity: the offer actually rendered some content.
    assert "ORCHESTA RFQ" in html
    assert "7200 zł" in html


def test_rendered_default_offer_html_has_no_telegram():
    html = rfo.render_offer_html(_complete_context())
    assert "Telegram" not in html
    assert "telegram" not in html.lower()


def test_tenant_template_file_has_no_telegram():
    template_path = ROOT / "tenants" / "orchesta" / "templates" / "orchesta_offer.html.j2"
    assert template_path.exists()
    content = template_path.read_text(encoding="utf-8")
    assert "Telegram" not in content
    assert "telegram" not in content.lower()
