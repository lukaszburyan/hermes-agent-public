#!/usr/bin/env python3
"""Build draft-only Orchesta final-offer artifacts.

This helper is intentionally local and deterministic. It does not send email,
does not call Telegram, and does not write Obsidian unless explicitly invoked
with --write-obsidian and --vault.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except Exception as exc:  # pragma: no cover - covered by dependency checks
    Environment = None  # type: ignore[assignment]
    FileSystemLoader = None  # type: ignore[assignment]
    StrictUndefined = None  # type: ignore[assignment]
    JINJA_IMPORT_ERROR = exc
else:
    JINJA_IMPORT_ERROR = None


SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = SKILL_DIR / "templates"
APPROVED_DIR = SKILL_DIR / "rfq-final-offer-knowledge" / "approved"
DEFAULT_PRICING_PATH = APPROVED_DIR / "pricing.json"
DEFAULT_TEMPLATE = "orchesta_offer.html.j2"
DEFAULT_CSS = "orchesta_offer.css"
DEFAULT_ACCOUNT_EMAIL = "rfq-mailbox@example.invalid"
# Tenant-ized templates (spec section 2): a tenant may keep its offer
# template under tenants/<tenant>/templates/. Resolution falls back to
# the skill templates when the tenant has no override.
REPO_ROOT = SKILL_DIR.parent.parent
TENANTS_DIR = REPO_ROOT / "tenants"
EXECUTION_DIR = REPO_ROOT / "execution"
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from offer_readiness import resolve_final_offer_scope  # noqa: E402

FOOTER = """--
Łukasz Buryan

tel. +48 000 000 000
LinkedIn. example.invalid/orchesta-rfq"""

BANNED_OUTPUT_PATTERNS = {
    "SMS": r"\bSMS\b",
    "Zoho": r"\bZoho\b",
    "pilotaż": r"\bpilota[żz]\b",
    "termin ważności": r"termin(?:y)?\s+wa[żz]no[śs]ci",
    "warunki prawne": r"warunki\s+prawne",
    "case studies": r"case\s+stud(?:y|ies)",
    "empty_jinja": r"\{\{|\}\}",
}

REQUIRED_OUTPUT_PATTERNS = {
    "client": None,
    "company": None,
    "price": None,
    "scope": r"konto pocztowe|konta pocztowe|kont pocztowych",
    "payment": r"p[łl]atno[śs][ćc].*100% z g[oó]ry|100% z g[oó]ry.*p[łl]atno[śs][ćc]",
    "implementation": r"wdro[żz]enie.*14 dni|14 dni.*wdro[żz]enie",
    "guarantee": r"14 dni.*gwarancj|gwarancj.*14 dni",
    "footer": r"Łukasz Buryan",
}

REQUIRED_SAFETY_KEYS = {
    "thread_headers_valid": True,
    "sender_matches_thread": True,
    "attachments_safe": True,
    "prompt_injection_detected": False,
}

QUESTION_BY_FIELD = {
    "client.first_name": "Jak mam się zwracać do osoby kontaktowej?",
    "client.company": "Na jaką firmę mam przygotować ofertę?",
    "client.email": "Na jaki adres mailowy mam przypisać kontakt w ofercie?",
    "scope.mailbox_count": "Ile kont pocztowych ma śledzić system?",
    "scope.crm": "Czy uwzględnić integrację z CRM w ofercie?",
    "scope.inquiry_source": "Skąd trafiają zapytania o wycenę: mail, formularz czy oba źródła?",
    "scope.has_sample_requests": "Czy macie przykładowe zapytania o wycenę, na których można dopasować logikę?",
}

QUESTION_BY_FIELD_EN = {
    "client.first_name": "How should I address the contact person?",
    "client.company": "Which company should I prepare the offer for?",
    "client.email": "Which email address should I assign to the contact in the offer?",
    "scope.mailbox_count": "How many inboxes should the system monitor?",
    "scope.crm": "Should the offer include CRM integration?",
    "scope.inquiry_source": "Do quote requests come from email, a form, or both sources?",
    "scope.has_sample_requests": "Do you have sample quote requests that can be used to tune the logic?",
}

ENGLISH_FIRST_NAMES = {
    "John",
    "Olivia",
    "Parker",
    "Michael",
    "Emily",
    "James",
    "Robert",
    "William",
    "David",
    "Sarah",
    "Emma",
    "Daniel",
}

VOCATIVE_OVERRIDES = {
    "Adam": ("male", "Adamie"),
    "Adrian": ("male", "Adrianie"),
    "Agnieszka": ("female", "Agnieszko"),
    "Aleksandra": ("female", "Aleksandro"),
    "Alicja": ("female", "Alicjo"),
    "Andrzej": ("male", "Andrzeju"),
    "Anna": ("female", "Anno"),
    "Artur": ("male", "Arturze"),
    "Barbara": ("female", "Barbaro"),
    "Bartosz": ("male", "Bartoszu"),
    "Dawid": ("male", "Dawidzie"),
    "Ewa": ("female", "Ewo"),
    "Filip": ("male", "Filipie"),
    "Gabriel": ("male", "Gabrielu"),
    "Grzegorz": ("male", "Grzegorzu"),
    "Jacek": ("male", "Jacku"),
    "Jakub": ("male", "Jakubie"),
    "Jan": ("male", "Janie"),
    "Joanna": ("female", "Joanno"),
    "Julia": ("female", "Julio"),
    "Kamil": ("male", "Kamilu"),
    "Karol": ("male", "Karolu"),
    "Karolina": ("female", "Karolino"),
    "Katarzyna": ("female", "Katarzyno"),
    "Krzysztof": ("male", "Krzysztofie"),
    "Kuba": ("male", "Kubo"),
    "Lukasz": ("male", "Łukaszu"),
    "Maciej": ("male", "Macieju"),
    "Magdalena": ("female", "Magdaleno"),
    "Malgorzata": ("female", "Małgorzato"),
    "Małgorzata": ("female", "Małgorzato"),
    "Marcin": ("male", "Marcinie"),
    "Marek": ("male", "Marku"),
    "Maria": ("female", "Mario"),
    "Mariusz": ("male", "Mariuszu"),
    "Marta": ("female", "Marto"),
    "Mateusz": ("male", "Mateuszu"),
    "Michal": ("male", "Michale"),
    "Michał": ("male", "Michale"),
    "Monika": ("female", "Moniko"),
    "Natalia": ("female", "Natalio"),
    "Olaf": ("male", "Olafie"),
    "Patryk": ("male", "Patryku"),
    "Pawel": ("male", "Pawle"),
    "Paweł": ("male", "Pawle"),
    "Piotr": ("male", "Piotrze"),
    "Rafal": ("male", "Rafale"),
    "Rafał": ("male", "Rafale"),
    "Robert": ("male", "Robercie"),
    "Sebastian": ("male", "Sebastianie"),
    "Szymon": ("male", "Szymonie"),
    "Tomasz": ("male", "Tomaszu"),
    "Wiktor": ("male", "Wiktorze"),
    "Wojciech": ("male", "Wojciechu"),
    "Zbigniew": ("male", "Zbigniewie"),
    "Zofia": ("female", "Zofio"),
    "Łukasz": ("male", "Łukaszu"),
}

# ponytail: common Polish diminutives -> formal name, so business correspondence
# addresses the client formally (Panie Tomaszu) rather than the diminutive they
# signed with (Tomek -> Tomku). Finite, well-known set; extend as needed.
DIMINUTIVE_TO_FORMAL = {
    "Tomek": "Tomasz", "Tomaszek": "Tomasz", "Tomuś": "Tomasz",
    "Kasia": "Katarzyna", "Kaśka": "Katarzyna", "Kasiula": "Katarzyna",
    "Krysia": "Krystyna", "Krystka": "Krystyna",
    "Jola": "Jolanta", "Jolka": "Jolanta",
    "Piotrek": "Piotr", "Piotruś": "Piotr",
    "Jurek": "Jerzy", "Jurkiem": "Jerzy",
    "Maciek": "Maciej", "Maciuś": "Maciej",
    "Zbyszek": "Zbigniew", "Zbyszek": "Zbigniew",
    "Krzysiek": "Krzysztof", "Krzyś": "Krzysztof", "Krzysiek": "Krzysztof",
    "Rysiek": "Ryszard", "Ryś": "Ryszard",
    "Marysia": "Maria", "Maryska": "Maria",
    "Ania": "Anna", "Anka": "Anna", "Anulka": "Anna",
    "Basia": "Barbara", "Baśka": "Barbara",
    "Bartek": "Bartosz", "Bartuś": "Bartosz",
    "Kazik": "Kazimierz", "Kaziu": "Kazimierz",
    "Leszek": "Lech", "Leszek": "Lech",
    "Mietek": "Mieczysław",
    "Władek": "Władysław", "Władeczek": "Władysław",
    "Staś": "Stanisław", "Staszek": "Stanisław", "Stasiek": "Stanisław",
    "Grzesiek": "Grzegorz", "Grześ": "Grzegorz",
    "Michałek": "Michał",
    "Pawelek": "Paweł",
    "Rafełek": "Rafał",
    "Kamilek": "Kamil",
    "Adrianek": "Adrian",
}


class OfferError(ValueError):
    """Raised when an offer cannot be safely built."""


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise OfferError(f"expected JSON object: {path}")
    return payload


def approved_knowledge_snapshot() -> list[dict[str, str]]:
    """Record provenance without exposing approved file contents to customers."""
    snapshot: list[dict[str, str]] = []
    for path in sorted(APPROVED_DIR.iterdir()):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        snapshot.append({
            "source": str(path.relative_to(SKILL_DIR)),
            "version": hashlib.sha256(raw).hexdigest()[:16],
            "approved_at": dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc).isoformat(timespec="seconds"),
        })
    return snapshot


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_in(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"tak", "true", "yes", "1"}:
            return True
        if lowered in {"nie", "false", "no", "0"}:
            return False
    return None


def format_pln(amount: int | float) -> str:
    rounded = int(round(amount))
    return f"{rounded:,}".replace(",", " ") + " zł"


def mailbox_label(count: int) -> str:
    if count == 1:
        return "konto pocztowe"
    if 2 <= count <= 4:
        return "konta pocztowe"
    return "kont pocztowych"


def inquiry_source_label(value: str) -> str:
    return {
        "mail": "mail",
        "form": "formularz",
        "both": "mail i formularz",
        "unknown": "do ustalenia operacyjnie",
    }.get(value, value)


# ponytail: rotated cover-note variants so the final-offer email is not identical for
# every customer. Selected deterministically by offer_number -> stable per offer,
# varied across customers. On-brand: short, concrete, calm, no hype, no prices.
FINAL_OFFER_COVER_NOTES = [
    "w załączniku dodaję gotową ofertę wdrożenia systemu Orchesta.\n\nW razie akceptacji wystarczy odpowiedzieć na tę wiadomość.",
    "w załączniku przesyłam ofertę wdrożenia systemu Orchesta przygotowaną do ustalonego zakresu.\n\nJeśli oferta pasuje, wystarczy odpowiedzieć na tę wiadomość.",
    "w załączniku znajdziesz ofertę wdrożenia systemu Orchesta dopasowaną do naszej rozmowy.\n\nJeśli wszystko się zgadza, odpowiedz na tę wiadomość, a ruszymy dalej.",
    "przygotowaną ofertę wdrożenia systemu Orchesta dorzucam w załączniku.\n\nW razie akceptacji wystarczy odpowiedzieć na tę wiadomość.",
    "w załączniku dorzucam ofertę wdrożenia systemu Orchesta dopasowaną do ustalonego zakresu.\n\nJeśli oferta pasuje, odpowiedz na tę wiadomość.",
]

MISSING_DATA_INTROS = [
    "żeby przygotować krótką ofertę, potrzebuję doprecyzować",
    "żeby ułożyć ofertę, muszę doprecyzować",
    "do przygotowania oferty brakuje mi jeszcze",
    "żeby dobrze ująć zakres w ofercie, doprecyzuj proszę",
]

MISSING_DATA_OUTROS = [
    "Po tej odpowiedzi przygotuję ofertę.",
    "Po tej odpowiedzi prześlę gotową ofertę.",
    "Jak tylko odpowiesz, przygotuję ofertę.",
    "Gdy tylko uzupełnisz te punkty, prześlę ofertę.",
]


def pick_variant(variants: list[str], seed: str) -> str:
    """Deterministic variant selection: stable for a given seed, varied across seeds."""
    if not variants:
        return ""
    idx = int(hashlib.sha256(str(seed or "").encode("utf-8")).hexdigest(), 16) % len(variants)
    return variants[idx]


def infer_gender(first_name: str, explicit: str = "") -> str:
    name = DIMINUTIVE_TO_FORMAL.get(first_name.strip(), first_name.strip())
    if name in VOCATIVE_OVERRIDES:
        return VOCATIVE_OVERRIDES[name][0]
    if first_name.strip().endswith("a"):
        return "female"
    if explicit in {"male", "female", "neutral"}:
        return explicit
    return "male"


def vocative_name(first_name: str) -> str:
    name = DIMINUTIVE_TO_FORMAL.get(first_name.strip(), first_name.strip())
    if not name:
        return ""
    if name in VOCATIVE_OVERRIDES:
        return VOCATIVE_OVERRIDES[name][1]
    if name.endswith("a"):
        return name[:-1] + "o"
    if name.endswith("ek") and len(name) > 3:
        return name[:-2] + "ku"
    if name.endswith(("sz", "cz")):
        return name + "u"
    if name.endswith("r"):
        return name + "ze"
    if name.endswith("ł"):
        return name[:-1] + "le"
    if name.endswith(("el", "ol", "ej", "ij")):
        return name + "u"
    if name.endswith(("k", "g")):
        return name + "u"
    return name


def salutation(client: dict[str, Any]) -> str:
    first_name = str(client.get("first_name") or "").strip()
    if not first_name:
        return "Dzień dobry,"
    display_name = str(client.get("salutation_name") or vocative_name(first_name)).strip()
    gender = infer_gender(first_name, str(client.get("gender") or "").strip().lower())
    if gender == "female":
        return f"Dzień dobry Pani {display_name},"
    if gender == "male":
        return f"Dzień dobry Panie {display_name},"
    return "Dzień dobry,"


def customer_language(data: dict[str, Any]) -> str:
    explicit = str(data.get("language") or data.get("customer_language") or "").strip().lower()
    if explicit in {"en", "eng", "english"}:
        return "en"
    if explicit in {"pl", "polish", "polski"}:
        return "pl"
    client = data.get("client") or {}
    company = str(client.get("company") or "").strip().lower()
    english_company_tokens = ("components", "systems", "quote", "automation", "meridian", "northbridge")
    if any(token in company for token in english_company_tokens):
        return "en"
    return "pl"


def salutation_for_language(client: dict[str, Any], language: str) -> str:
    if language == "en":
        first_name = str(client.get("first_name") or "").strip()
        return f"Hello {first_name}," if first_name else "Hello,"
    return salutation(client)


def client_full_name(client: dict[str, Any]) -> str:
    parts = [str(client.get("first_name") or "").strip(), str(client.get("last_name") or "").strip()]
    full = " ".join(part for part in parts if part)
    return full or str(client.get("email") or "").strip() or "Klient"


def validate_required_data(data: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in (
        "client.company",
        "client.email",
    ):
        value = get_in(data, field)
        if is_blank(value):
            missing.append(field)

    mailbox_count = get_in(data, "scope.mailbox_count")
    if not is_blank(mailbox_count):
        try:
            if int(mailbox_count) < 1:
                missing.append("scope.mailbox_count")
        except (TypeError, ValueError):
            missing.append("scope.mailbox_count")

    source = str(get_in(data, "scope.inquiry_source", "") or "").strip()
    if source and source not in {"mail", "form", "both", "unknown"}:
        missing.append("scope.inquiry_source")

    return sorted(set(missing))


def apply_offer_readiness(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply only approved start variants and retain their provenance."""
    resolved_data = deepcopy(data)
    scope = dict(resolved_data.get("scope") or {})
    readiness = resolve_final_offer_scope(scope)
    scope.update(readiness.get("resolved_facts") or {})
    scope["fact_states"] = readiness.get("fact_states") or {}
    scope["assumptions"] = readiness.get("assumptions") or []
    resolved_data["scope"] = scope
    resolved_data["offer_readiness"] = readiness
    return resolved_data, readiness


def validate_safety(data: dict[str, Any], pricing_path: Path) -> list[str]:
    blocks: list[str] = []
    safety = data.get("safety")
    if not isinstance(safety, dict):
        return ["missing_safety_context"]

    for key, expected in REQUIRED_SAFETY_KEYS.items():
        if key not in safety:
            blocks.append(f"missing_safety.{key}")
        elif to_bool(safety.get(key)) is not expected:
            blocks.append(f"safety_failed.{key}")

    confidence = str(safety.get("classification_confidence") or "").strip().lower()
    if confidence not in {"high", "medium"}:
        blocks.append("classification_confidence_low_or_missing")

    expectations = data.get("customer_expectations") or {}
    if isinstance(expectations, dict):
        if to_bool(expectations.get("expects_sms")) is True:
            blocks.append("customer_expects_sms")
        if to_bool(expectations.get("expects_full_automatic_technical_pricing")) is True:
            blocks.append("customer_expects_full_automatic_technical_pricing")
        if to_bool(expectations.get("expects_auto_send_final_offers")) is True:
            blocks.append("customer_expects_auto_send_final_offers")

    if "approved" not in pricing_path.parts or pricing_path.name != "pricing.json":
        blocks.append("pricing_not_from_approved_pricing_json")

    if data.get("knowledge_conflicts"):
        blocks.append("knowledge_conflict")
    for exception in data.get("commercial_exception") or []:
        blocks.append(f"commercial_exception.{exception}")

    return sorted(set(blocks))


def missing_questions(missing: list[str], blocks: list[str], *, language: str = "pl") -> list[str]:
    question_by_field = QUESTION_BY_FIELD_EN if language == "en" else QUESTION_BY_FIELD
    questions: list[str] = []
    if "scope.mailbox_count" in missing:
        questions.append(question_by_field["scope.mailbox_count"])

    if "scope.crm" in missing:
        questions.append(question_by_field["scope.crm"])

    for field in ("client.company", "client.email"):
        if field in missing and question_by_field[field] not in questions:
            questions.append(question_by_field[field])

    return questions[:2]


def question_count_word(count: int) -> str:
    return {1: "jedną rzecz", 2: "dwie rzeczy", 3: "trzy rzeczy"}.get(count, "kilka rzeczy")


def ensure_pricing(pricing: dict[str, Any]) -> None:
    required = [
        ("base_offer", "net_price"),
        ("additional_mailbox", "net_price"),
        ("crm_integration", "net_price"),
    ]
    for section, key in required:
        if not isinstance(pricing.get(section), dict) or not isinstance(pricing[section].get(key), int):
            raise OfferError(f"invalid pricing file: missing {section}.{key}")


def calculate_pricing(scope: dict[str, Any], pricing: dict[str, Any]) -> dict[str, Any]:
    ensure_pricing(pricing)
    mailbox_count = int(scope["mailbox_count"])
    crm = bool(to_bool(scope.get("crm")))
    base_price = int(pricing["base_offer"]["net_price"])
    extra_count = max(0, mailbox_count - int(pricing["base_offer"].get("includes_mailboxes", 1)))
    extra_price = int(pricing["additional_mailbox"]["net_price"])
    crm_price = int(pricing["crm_integration"]["net_price"])

    line_items = [
        {
            "name": pricing["base_offer"]["name"],
            "quantity": 1,
            "net_price": base_price,
            "net_display": format_pln(base_price),
        }
    ]
    if extra_count:
        total = extra_count * extra_price
        line_items.append(
            {
                "name": f"{pricing['additional_mailbox']['name']} ({extra_count} × {format_pln(extra_price)})",
                "quantity": extra_count,
                "net_price": total,
                "net_display": format_pln(total),
            }
        )
    if crm:
        line_items.append(
            {
                "name": pricing["crm_integration"]["name"],
                "quantity": 1,
                "net_price": crm_price,
                "net_display": format_pln(crm_price),
            }
        )

    total = sum(int(item["net_price"]) for item in line_items)
    result = {
        "currency": pricing.get("currency", "PLN"),
        "tax_mode": "net",
        "line_items": line_items,
        "net_total": total,
        "net_total_display": format_pln(total),
        "pricing_source": str(DEFAULT_PRICING_PATH.relative_to(SKILL_DIR)),
        "pricing_id": pricing.get("pricing_id"),
    }
    if str((scope.get("fact_states") or {}).get("crm", {}).get("state") or "") == "assumed":
        result["optional_variants"] = [{
            "name": pricing["crm_integration"]["name"],
            "net_price": crm_price,
            "net_display": format_pln(crm_price),
            "total_if_selected": total + crm_price,
            "total_if_selected_display": format_pln(total + crm_price),
        }]
    else:
        result["optional_variants"] = []
    return result


def roi_context(data: dict[str, Any], pricing: dict[str, Any]) -> dict[str, str]:
    defaults = pricing.get("roi_defaults") or {}
    hourly = int(defaults.get("hourly_cost_gross", 55))
    minutes_min = int(defaults.get("minutes_per_response_min", 30))
    minutes_max = int(defaults.get("minutes_per_response_max", 120))
    provided = get_in(data, "scope.monthly_volume")
    if provided is None:
        provided = get_in(data, "scope.monthly_inquiries")
    if provided is not None:
        try:
            monthly = int(provided)
        except (TypeError, ValueError):
            monthly = 0
        if monthly > 0:
            hours_min = monthly * minutes_min / 60
            hours_max = monthly * minutes_max / 60
            return {
                "title": "Szacunkowy potencjał oszczędności czasu",
                "description": f"Dla {monthly} zapytań o wycenę miesięcznie i {minutes_min}-{minutes_max} minut pracy nad odpowiedzią.",
                "hours_text": f"Zakres pracy: {hours_min:g}-{hours_max:g} h miesięcznie.",
                "cost_text": f"Stała kalkulacyjna kosztu pracy: {format_pln(hours_min * hourly)}-{format_pln(hours_max * hourly)} brutto miesięcznie.",
            }

    min_inquiries = int(defaults.get("monthly_inquiries_min", 20))
    max_inquiries = int(defaults.get("monthly_inquiries_max", 30))
    min_hours_a = min_inquiries * minutes_min / 60
    max_hours_a = min_inquiries * minutes_max / 60
    min_hours_b = max_inquiries * minutes_min / 60
    max_hours_b = max_inquiries * minutes_max / 60
    return {
        "title": "Przykład kalkulacyjny",
        "description": f"Przy {min_inquiries}-{max_inquiries} zapytaniach o wycenę miesięcznie i {minutes_min}-{minutes_max} minutach pracy nad odpowiedzią.",
        "hours_text": f"{min_inquiries} zapytań: {min_hours_a:g}-{max_hours_a:g} h. {max_inquiries} zapytań: {min_hours_b:g}-{max_hours_b:g} h miesięcznie.",
        "cost_text": f"Koszt pracy: {format_pln(min_hours_a * hourly)}-{format_pln(max_hours_a * hourly)} oraz {format_pln(min_hours_b * hourly)}-{format_pln(max_hours_b * hourly)} brutto miesięcznie. To nie jest gwarantowana oszczędność.",
    }


def build_offer_context(data: dict[str, Any], pricing_file: dict[str, Any]) -> dict[str, Any]:
    language = customer_language(data)
    today = str(data.get("date") or dt.date.today().isoformat())
    year = today[:4]
    sequence = int(data.get("sequence") or 1)
    offer_number = str(data.get("offer_number") or f"ORCH-RFQ-{year}-{sequence:04d}")
    client = deepcopy(data["client"])
    client["full_name"] = client_full_name(client)
    scope = deepcopy(data["scope"])
    scope["mailbox_count"] = int(scope["mailbox_count"])
    scope["crm"] = bool(to_bool(scope["crm"]))
    scope["has_sample_requests"] = to_bool(scope.get("has_sample_requests"))
    scope["inquiry_source"] = str(scope.get("inquiry_source") or "unknown").strip()
    scope["mailbox_label"] = mailbox_label(scope["mailbox_count"])
    crm_assumed = str((scope.get("fact_states") or {}).get("crm", {}).get("state") or "") == "assumed"
    scope["crm_label"] = "opcja do potwierdzenia" if crm_assumed else ("tak" if scope["crm"] else "nie")
    scope["inquiry_source_label"] = inquiry_source_label(str(scope["inquiry_source"]))
    pricing = calculate_pricing(scope, pricing_file)
    pricing_version = str(pricing_file.get("pricing_id") or "unknown")
    pricing_hash = hashlib.sha256(json.dumps(pricing_file, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    scope_hash = hashlib.sha256(json.dumps(scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    customer_hash = hashlib.sha256(json.dumps(client, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    version = str(data.get("version") or "v1")
    if not version.startswith("v"):
        version = f"v{version}"
    deal_id = str(data.get("deal_id") or (data.get("thread") or {}).get("thread_id") or "")
    offer_id = str(data.get("offer_id") or f"{offer_number}-{version}")
    summary = (
        f"Oferta dla {client['company']}: {scope['mailbox_count']} {scope['mailbox_label']}, "
        f"CRM: {scope['crm_label']}, cena {pricing['net_total_display']} netto."
    )
    return {
        "offer": {
            "offer_number": offer_number,
            "date": today,
            "version": version,
            "offer_id": offer_id,
            "deal_id": deal_id,
            "pricing_version": pricing_version,
            "pricing_hash": pricing_hash,
            "scope_hash": scope_hash,
            "customer_data_hash": customer_hash,
            "supersedes_version": str(data.get("supersedes_version") or "") or None,
            "status": "ready_for_offer",
            "summary": summary,
        },
        "client": client,
        "scope": scope,
        "readiness": deepcopy(data.get("offer_readiness") or {}),
        "assumptions": deepcopy(scope.get("assumptions") or []),
        "pricing": pricing,
        "roi": roi_context(data, pricing_file),
        "footer": FOOTER,
        "language": language,
        "salutation": salutation_for_language(client, language),
        "cover_note": pick_variant(FINAL_OFFER_COVER_NOTES, str(offer_number)),
        "account_email": str(data.get("account_email") or DEFAULT_ACCOUNT_EMAIL),
        "thread": data.get("thread") or {},
        "safety": data.get("safety") or {},
        "templates": {
            "html": f"templates/{DEFAULT_TEMPLATE}",
            "css": f"templates/{DEFAULT_CSS}",
            "version": "v1",
        },
        "knowledge_sources": approved_knowledge_snapshot(),
    }


def jinja_env() -> Environment:
    if JINJA_IMPORT_ERROR is not None:
        raise OfferError(f"Jinja2 is required: {JINJA_IMPORT_ERROR}")
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def tenant_template_dir(tenant_id: str | None) -> Path | None:
    """Return the tenant templates dir if it exists, else None."""
    if not tenant_id:
        return None
    candidate = TENANTS_DIR / tenant_id / "templates"
    return candidate if candidate.is_dir() else None


def resolve_offer_template(tenant_id: str | None) -> tuple[Path, str, Path]:
    """Resolve (template_dir, template_name, css_path) for a tenant.

    Prefers tenants/<tenant>/templates/orchesta_offer.html.j2; falls back to
    the skill templates dir. The CSS falls back to the skill CSS when the
    tenant package does not ship its own.
    """
    tdir = tenant_template_dir(tenant_id)
    if tdir and (tdir / DEFAULT_TEMPLATE).exists():
        css = tdir / DEFAULT_CSS
        if not css.exists():
            css = TEMPLATE_DIR / DEFAULT_CSS
        return tdir, DEFAULT_TEMPLATE, css
    return TEMPLATE_DIR, DEFAULT_TEMPLATE, TEMPLATE_DIR / DEFAULT_CSS


def render_template(template_name: str, context: dict[str, Any]) -> str:
    return jinja_env().get_template(template_name).render(**context)


def render_offer_html(context: dict[str, Any]) -> str:
    """Legacy entry point: renders with the default (skill) template."""
    css = (TEMPLATE_DIR / DEFAULT_CSS).read_text(encoding="utf-8")
    return render_template(DEFAULT_TEMPLATE, {**context, "css": css})


def _tenant_brand_defaults(tenant_id: str | None) -> dict[str, Any]:
    """Read seller branding from the tenant's company.yaml (spec section 20).

    The PDF must show the seller's company data, sourced from the tenant
    package rather than hardcoded. Returns an empty dict when the tenant has
    no company.yaml (callers' explicit context values then win).
    """
    if not tenant_id:
        return {}
    try:
        import sys as _sys
        _repo = SKILL_DIR.parent.parent
        if str(_repo / "execution") not in _sys.path:
            _sys.path.insert(0, str(_repo / "execution"))
        import tenant_config as _tc  # type: ignore[import-not-at-top]
    except Exception:
        return {}
    try:
        loaded = _tc.load_company(tenant_id) or {}
    except Exception:
        return {}
    company = loaded.get("company") or {}
    name = str(company.get("name") or "").strip()
    if not name:
        return {}
    return {"brand": f"{name.upper()} RFQ", "brand_name": name}


def render_offer_html_for_tenant(context: dict[str, Any], tenant_id: str | None = None) -> str:
    """Tenant-ized offer HTML (spec section 2/11).

    Uses tenants/<tenant>/templates/orchesta_offer.html.j2 when present,
    falling back to the skill template. The CSS is resolved the same way.
    Seller branding (``brand``, ``brand_name``) is injected from the tenant's
    company.yaml unless the caller already supplies it.
    """
    if JINJA_IMPORT_ERROR is not None:
        raise OfferError(f"Jinja2 is required: {JINJA_IMPORT_ERROR}")
    template_dir, template_name, css_path = resolve_offer_template(tenant_id)
    css = css_path.read_text(encoding="utf-8") if css_path.exists() else ""
    defaults = _tenant_brand_defaults(tenant_id)
    merged = {**defaults, **context, "css": css}
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    return env.get_template(template_name).render(**merged)


def render_pdf(html_path: Path, pdf_path: Path) -> None:
    try:
        from weasyprint import HTML
    except Exception as exc:  # pragma: no cover - environment dependent
        raise OfferError(f"WeasyPrint is not installed or cannot start: {exc}") from exc
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(filename=str(html_path), base_url=str(SKILL_DIR)).write_pdf(str(pdf_path))


def extract_pdf_text_and_pages(pdf_path: Path) -> tuple[str, int | None, list[str]]:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - environment dependent
        return "", None, [f"pypdf unavailable; skipped PDF text/page validation: {exc}"]
    reader = PdfReader(str(pdf_path))
    text_parts = []
    warnings: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        text_parts.append(page_text)
        if not normalize_spaces(page_text):
            warnings.append("empty_page")
    return "\n".join(text_parts), len(reader.pages), warnings


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def validate_output_text(text: str, context: dict[str, Any], page_count: int | None = None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    normalized = text or ""
    normalized_single_line = normalize_spaces(normalized)

    dynamic_required = {
        "client": str(context["client"]["full_name"]),
        "company": str(context["client"]["company"]),
        "offer_number": str(context["offer"]["offer_number"]),
        "price": str(context["pricing"]["net_total_display"]),
        "version": str(context["offer"]["version"]),
    }
    normalized_single_line_casefold = normalized_single_line.casefold()
    for label, expected in dynamic_required.items():
        expected_text = normalize_spaces(expected)
        if expected_text.casefold() not in normalized_single_line_casefold:
            errors.append(f"missing_required_text:{label}")

    required = {
        label: pattern
        for label, pattern in REQUIRED_OUTPUT_PATTERNS.items()
        if label not in dynamic_required
    }
    for label, pattern in required.items():
        if pattern and not re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL):
            errors.append(f"missing_required_text:{label}")

    banned_scan_text = normalized
    company_text = str(context["client"].get("company") or "")
    if company_text:
        banned_scan_text = normalize_spaces(banned_scan_text).replace(normalize_spaces(company_text), "")

    for label, pattern in BANNED_OUTPUT_PATTERNS.items():
        if re.search(pattern, banned_scan_text, flags=re.IGNORECASE):
            errors.append(f"forbidden_text:{label}")

    if page_count is not None and not (3 <= page_count <= 4):
        errors.append(f"invalid_page_count:{page_count}")

    return {"ok": not errors, "errors": errors, "warnings": warnings, "page_count": page_count}


def validate_html(html: str, context: dict[str, Any]) -> dict[str, Any]:
    pages = len(re.findall(r"<section\s+class=\"page", html))
    return validate_output_text(html, context, pages)


def validate_pdf(pdf_path: Path, context: dict[str, Any]) -> dict[str, Any]:
    if not pdf_path.is_file():
        return {"ok": False, "errors": ["pdf_missing"], "warnings": [], "page_count": None}
    if pdf_path.stat().st_size <= 0:
        return {"ok": False, "errors": ["pdf_empty"], "warnings": [], "page_count": None}
    try:
        if pdf_path.read_bytes()[:4] != b"%PDF":
            return {"ok": False, "errors": ["pdf_invalid_header"], "warnings": [], "page_count": None}
        text, pages, warnings = extract_pdf_text_and_pages(pdf_path)
    except Exception as exc:
        return {
            "ok": False,
            "errors": [f"pdf_open_failed:{exc.__class__.__name__}"],
            "warnings": [],
            "page_count": None,
        }
    if not text or pages is None:
        return {"ok": False, "errors": ["pdf_text_validation_unavailable"], "warnings": warnings, "page_count": pages}
    result = validate_output_text(text, context, pages)
    result["warnings"].extend(warnings)
    if "empty_page" in warnings:
        result["errors"].append("empty_page")
        result["ok"] = False
    return result


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^\w .-]+", "", value, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Unknown"


def offer_json_payload(context: dict[str, Any], *, draft_id: str = "") -> dict[str, Any]:
    return {
        "offer_number": context["offer"]["offer_number"],
        "date": context["offer"]["date"],
        "version": context["offer"]["version"],
        "offer_id": context["offer"]["offer_id"],
        "deal_id": context["offer"]["deal_id"],
        "pricing_version": context["offer"]["pricing_version"],
        "pricing_hash": context["offer"]["pricing_hash"],
        "scope_hash": context["offer"]["scope_hash"],
        "customer_data_hash": context["offer"]["customer_data_hash"],
        "supersedes_version": context["offer"]["supersedes_version"],
        "client": {
            "first_name": context["client"].get("first_name"),
            "last_name": context["client"].get("last_name"),
            "full_name": context["client"].get("full_name"),
            "company": context["client"].get("company"),
            "email": context["client"].get("email"),
        },
        "scope": {
            "mailbox_count": context["scope"]["mailbox_count"],
            "crm": context["scope"]["crm"],
            "inquiry_source": context["scope"]["inquiry_source"],
            "has_sample_requests": context["scope"]["has_sample_requests"],
        },
        "pricing": context["pricing"],
        "templates": context["templates"],
        "status": context["offer"]["status"],
        "draft_id": draft_id,
        "source_thread": context["thread"],
        "summary": context["offer"]["summary"],
        "safety_gates": context["safety"],
        "knowledge_sources": context.get("knowledge_sources", []),
    }


def write_obsidian_crm(vault: Path, context: dict[str, Any], offer_payload: dict[str, Any]) -> dict[str, str]:
    crm = vault / "CRM"
    for folder in (
        "Companies",
        "People",
        "Deals",
        "Offers",
        "Activity",
        "Knowledge/Approved",
        "Knowledge/Proposed",
        "Knowledge/Rejected",
        "Logs",
    ):
        (crm / folder).mkdir(parents=True, exist_ok=True)

    company_name = safe_filename(str(context["client"]["company"]))
    person_name = safe_filename(str(context["client"]["full_name"]))
    offer_number = str(context["offer"]["offer_number"])
    deal_path = crm / "Deals" / f"{offer_number} {company_name}.md"
    company_path = crm / "Companies" / f"{company_name}.md"
    person_path = crm / "People" / f"{person_name}.md"
    offer_path = crm / "Offers" / f"{offer_number}.json"
    activity_path = crm / "Activity" / f"{offer_number} offer draft.md"

    company_path.write_text(
        "\n".join(
            [
                f"# {context['client']['company']}",
                "",
                f"- źródło zapytania: {context['scope']['inquiry_source_label']}",
                f"- osoby kontaktowe: {context['client']['full_name']}",
                f"- otwarte deale: {offer_number}",
                "",
                "## Notatki",
                context["offer"]["summary"],
                "",
            ]
        ),
        encoding="utf-8",
    )

    person_path.write_text(
        "\n".join(
            [
                f"# {context['client']['full_name']}",
                "",
                f"- imię: {context['client'].get('first_name', '')}",
                f"- nazwisko: {context['client'].get('last_name', '')}",
                f"- email: {context['client'].get('email', '')}",
                f"- firma: {context['client'].get('company', '')}",
                f"- wątki mailowe: {context['thread'].get('thread_id', '')}",
                f"- correlation_id: {context['thread'].get('correlation_id', '')}",
                "",
                "## Notatki",
                context["offer"]["summary"],
                "",
            ]
        ),
        encoding="utf-8",
    )

    deal_path.write_text(
        "\n".join(
            [
                f"# {offer_number} {context['client']['company']}",
                "",
                f"- status: offer_draft_created",
                f"- klient: {context['client']['full_name']}",
                f"- firma: {context['client']['company']}",
                f"- email: {context['client']['email']}",
                f"- źródło zapytania: {context['scope']['inquiry_source_label']}",
                f"- liczba kont: {context['scope']['mailbox_count']}",
                f"- CRM: {context['scope']['crm_label']}",
                f"- cena netto: {context['pricing']['net_total_display']}",
                f"- data ostatniego kontaktu: {context['offer']['date']}",
                f"- numer oferty: {offer_number}",
                f"- link do offer JSON: CRM/Offers/{offer_number}.json",
                f"- correlation_id: {context['thread'].get('correlation_id', '')}",
                "",
                "## Podsumowanie",
                context["offer"]["summary"],
                "",
            ]
        ),
        encoding="utf-8",
    )

    if offer_path.exists():
        record = load_json(offer_path)
        versions = record.get("versions")
        if not isinstance(versions, list):
            versions = []
    else:
        record = {"offer_number": offer_number, "versions": []}
        versions = record["versions"]

    version_payload = deepcopy(offer_payload)
    requested_version = str(context["offer"].get("version") or f"v{len(versions) + 1}")
    if any(str(item.get("version")) == requested_version for item in versions if isinstance(item, dict)):
        raise OfferError(f"offer version already exists: {requested_version}")
    version_payload["version"] = requested_version
    version_payload["status"] = "offer_draft_created"
    versions.append(version_payload)
    record["versions"] = versions
    record["latest_version"] = version_payload["version"]
    write_json(offer_path, record)

    activity_path.write_text(
        "\n".join(
            [
                f"# {offer_number} draft oferty",
                "",
                f"- data: {context['offer']['date']}",
                f"- status: offer_draft_created",
                f"- cena: {context['pricing']['net_total_display']} netto",
                f"- zakres: {context['scope']['mailbox_count']} {context['scope']['mailbox_label']}, CRM: {context['scope']['crm_label']}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "company": str(company_path),
        "person": str(person_path),
        "deal": str(deal_path),
        "offer": str(offer_path),
        "activity": str(activity_path),
    }


def build_blocked_outputs(data: dict[str, Any], missing: list[str], blocks: list[str]) -> dict[str, Any]:
    client = deepcopy(data.get("client") or {})
    client.setdefault("company", "")
    client["full_name"] = client_full_name(client)
    language = customer_language(data)
    questions = missing_questions(missing, [], language=language) if not blocks else []
    context = {
        "client": client,
        "salutation": salutation_for_language(client, language),
        "questions": questions,
        "question_count_word": question_count_word(len(questions)),
        "footer": FOOTER,
        "reason": ", ".join(blocks or missing),
        "missing_data_intro": pick_variant(MISSING_DATA_INTROS, str(client.get("email") or client.get("company") or "")),
        "missing_data_outro": pick_variant(MISSING_DATA_OUTROS, str(client.get("email") or client.get("company") or "")),
    }
    template = "mail_missing_data_en.txt.j2" if language == "en" else "mail_missing_data_pl.txt.j2"
    email = render_template(template, context) if questions else ""
    telegram = render_template("telegram_blocked_pl.txt.j2", context)
    return {
        "status": "blocked" if blocks else "awaiting_data",
        "missing": missing,
        "blocks": blocks,
        "questions": questions,
        "mail_missing_data": email,
        "telegram": telegram,
    }


def build_success_outputs(context: dict[str, Any]) -> dict[str, str]:
    template = "mail_final_offer_en.txt.j2" if context.get("language") == "en" else "mail_final_offer_pl.txt.j2"
    mail = render_template(template, context)
    telegram = render_template("telegram_pdf_created_pl.txt.j2", context)
    return {"mail_final_offer": mail, "telegram": telegram}


def run(input_path: Path, output_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    data, readiness = apply_offer_readiness(load_json(input_path))
    pricing_path = Path(args.pricing).resolve() if args.pricing else DEFAULT_PRICING_PATH
    pricing = load_json(pricing_path)
    missing = validate_required_data(data)
    blocks = validate_safety(data, pricing_path)
    blocks.extend(f"offer_fact_conflict.{field}" for field in readiness.get("blocking_fields") or [])
    blocks = sorted(set(blocks))
    output_dir.mkdir(parents=True, exist_ok=True)

    if missing or blocks:
        blocked = build_blocked_outputs(data, missing, blocks)
        if blocked.get("mail_missing_data"):
            (output_dir / "mail_missing_data.txt").write_text(blocked["mail_missing_data"], encoding="utf-8")
        (output_dir / "telegram_blocked.txt").write_text(blocked["telegram"], encoding="utf-8")
        manifest = {"input": str(input_path), "output_dir": str(output_dir), "readiness": readiness, **blocked}
        write_json(output_dir / "manifest.json", manifest)
        return manifest

    context = build_offer_context(data, pricing)
    offer_payload = offer_json_payload(context, draft_id=str(get_in(data, "thread.draft_id", "")))
    offer_json_path = output_dir / f"{context['offer']['offer_number']}.json"
    write_json(offer_json_path, offer_payload)

    html_path = output_dir / f"{context['offer']['offer_number']}.html"
    pdf_path = output_dir / f"{context['offer']['offer_number']}.pdf"
    html = render_offer_html(context)
    html_path.write_text(html, encoding="utf-8")
    html_validation = validate_html(html, context)
    if not html_validation["ok"]:
        manifest = {
            "status": "blocked",
            "reason": "html_validation_failed",
            "html_validation": html_validation,
            "offer_json": str(offer_json_path),
            "html": str(html_path),
        }
        write_json(output_dir / "manifest.json", manifest)
        return manifest

    pdf_validation: dict[str, Any] | None = None
    if not args.render_pdf:
        manifest = {
            "status": "blocked",
            "reason": "pdf_required_for_final_offer",
            "html_validation": html_validation,
            "offer_json": str(offer_json_path),
            "html": str(html_path),
            "pdf": "",
        }
        write_json(output_dir / "manifest.json", manifest)
        return manifest

    try:
        render_pdf(html_path, pdf_path)
    except OfferError as exc:
        if not args.allow_missing_weasyprint:
            raise
        pdf_validation = {"ok": False, "errors": [], "warnings": [str(exc)], "page_count": None}
        manifest = {
            "status": "blocked",
            "reason": "pdf_render_unavailable",
            "pdf_validation": pdf_validation,
            "offer_json": str(offer_json_path),
            "html": str(html_path),
            "pdf": "",
        }
        write_json(output_dir / "manifest.json", manifest)
        return manifest
    else:
        pdf_validation = validate_pdf(pdf_path, context)
        if not pdf_validation["ok"]:
            manifest = {
                "status": "blocked",
                "reason": "pdf_validation_failed",
                "pdf_validation": pdf_validation,
                "offer_json": str(offer_json_path),
                "html": str(html_path),
                "pdf": str(pdf_path),
            }
            write_json(output_dir / "manifest.json", manifest)
            return manifest

    pdf_checksum = ""
    pdf_size = 0
    if pdf_path.exists():
        pdf_bytes = pdf_path.read_bytes()
        pdf_checksum = hashlib.sha256(pdf_bytes).hexdigest()
        pdf_size = len(pdf_bytes)
        offer_payload["pdf_sha256"] = pdf_checksum
        offer_payload["pdf_size_bytes"] = pdf_size
        write_json(offer_json_path, offer_payload)

    messages = build_success_outputs(context)
    (output_dir / "mail_final_offer.txt").write_text(messages["mail_final_offer"], encoding="utf-8")
    (output_dir / "telegram_pdf_created.txt").write_text(messages["telegram"], encoding="utf-8")

    obsidian_paths: dict[str, str] = {}
    if args.write_obsidian:
        if not args.vault:
            raise OfferError("--write-obsidian requires --vault")
        obsidian_paths = write_obsidian_crm(Path(args.vault), context, offer_payload)

    if args.delete_pdf_after_success and pdf_path.exists():
        pdf_path.unlink()

    manifest = {
        "status": "offer_draft_created",
        "offer_number": context["offer"]["offer_number"],
        "offer_id": context["offer"]["offer_id"],
        "deal_id": context["offer"]["deal_id"],
        "version": context["offer"]["version"],
        "pricing_version": context["offer"]["pricing_version"],
        "pricing_hash": context["offer"]["pricing_hash"],
        "scope_hash": context["offer"]["scope_hash"],
        "customer_data_hash": context["offer"]["customer_data_hash"],
        "pdf_sha256": pdf_checksum,
        "pdf_size_bytes": pdf_size,
        "price_net": context["pricing"]["net_total"],
        "price_net_display": context["pricing"]["net_total_display"],
        "offer_json": str(offer_json_path),
        "html": str(html_path),
        "pdf": str(pdf_path) if pdf_path.exists() else "",
        "html_validation": html_validation,
        "pdf_validation": pdf_validation,
        "mail_final_offer": str(output_dir / "mail_final_offer.txt"),
        "telegram": str(output_dir / "telegram_pdf_created.txt"),
        "obsidian": obsidian_paths,
        "readiness": readiness,
        "assumptions": context.get("assumptions") or [],
    }
    write_json(output_dir / "manifest.json", manifest)
    return manifest


def sample_data(mailbox_count: int, crm: bool) -> dict[str, Any]:
    return {
        "offer_number": "ORCH-RFQ-2026-0001",
        "date": "2026-07-02",
        "sequence": 1,
        "account_email": DEFAULT_ACCOUNT_EMAIL,
        "client": {
            "first_name": "Tomasz",
            "last_name": "Nowak",
            "company": "ACME",
            "email": "tomasz.nowak@example.com",
            "gender": "male",
        },
        "scope": {
            "mailbox_count": mailbox_count,
            "crm": crm,
            "inquiry_source": "mail",
            "has_sample_requests": True,
        },
        "thread": {
            "thread_id": "thread-123",
            "source_message_id": "<message@example.com>",
        },
        "safety": {
            "thread_headers_valid": True,
            "sender_matches_thread": True,
            "attachments_safe": True,
            "prompt_injection_detected": False,
            "classification_confidence": "high",
        },
        "customer_expectations": {
            "expects_sms": False,
            "expects_full_automatic_technical_pricing": False,
            "expects_auto_send_final_offers": False,
        },
    }


def self_test() -> int:
    failures: list[str] = []
    pricing = load_json(DEFAULT_PRICING_PATH)

    examples = [
        (1, False, 7200),
        (2, False, 10200),
        (3, True, 16200),
    ]
    for mailbox_count, crm, expected in examples:
        context = build_offer_context(sample_data(mailbox_count, crm), pricing)
        got = context["pricing"]["net_total"]
        if got != expected:
            failures.append(f"price {mailbox_count}/{crm}: expected {expected}, got {got}")

    context = build_offer_context(sample_data(2, False), pricing)
    html = render_offer_html(context)
    validation = validate_html(html, context)
    if not validation["ok"]:
        failures.append(f"html validation failed: {validation}")
    long_company_data = sample_data(4, False)
    long_company_data["client"]["first_name"] = "Krzysztof"
    long_company_data["client"]["last_name"] = "Wojcik"
    long_company_data["client"]["company"] = "Polnocne Centrum Automatyzacji Testowej Sp. z o.o."
    long_company_context = build_offer_context(long_company_data, pricing)
    wrapped_pdf_text = (
        "Krzysztof Wojcik, Polnocne Centrum Automatyzacji\n"
        "Testowej Sp. z o.o.\n"
        f"{long_company_context['offer']['offer_number']}\n"
        "Wersja v1\n"
        "16 200 zł netto\n"
        "konto pocztowe\n"
        "Płatność: 100% z góry.\n"
        "Wdrożenie: 14 dni.\n"
        "14 dni gwarancji\n"
        f"{FOOTER}\n"
    )
    wrapped_validation = validate_output_text(wrapped_pdf_text, long_company_context, page_count=4)
    if not wrapped_validation["ok"]:
        failures.append(f"wrapped PDF text validation failed: {wrapped_validation}")
    if re.search(BANNED_OUTPUT_PATTERNS["SMS"], html, flags=re.IGNORECASE):
        failures.append("html contains forbidden SMS")
    css_text = (TEMPLATE_DIR / DEFAULT_CSS).read_text(encoding="utf-8")
    if "height: 297mm" not in css_text or "padding: 22mm 24mm 34mm" not in css_text:
        failures.append("pdf CSS must reserve a fixed A4 page and footer-safe bottom area")

    bad_data = sample_data(1, False)
    bad_data["scope"].pop("crm")
    resolved_bad_data, bad_readiness = apply_offer_readiness(bad_data)
    if validate_required_data(resolved_bad_data):
        failures.append("unknown CRM should not block the approved base offer")
    if not any(item.get("field") == "crm" for item in bad_readiness.get("assumptions") or []):
        failures.append(f"unknown CRM should create an explicit optional variant: {bad_readiness}")

    minimum_data = sample_data(1, False)
    minimum_data["client"]["first_name"] = ""
    minimum_data["client"]["last_name"] = ""
    minimum_data["scope"].pop("inquiry_source")
    minimum_data["scope"].pop("has_sample_requests")
    minimum_missing = validate_required_data(minimum_data)
    if minimum_missing:
        failures.append(f"non-pricing helper fields should not block PDF: {minimum_missing}")
    minimum_context = build_offer_context(minimum_data, pricing)
    if minimum_context["salutation"] != "Dzień dobry,":
        failures.append(f"missing first name should use generic salutation: {minimum_context['salutation']}")
    if minimum_context["scope"]["inquiry_source_label"] != "do ustalenia operacyjnie":
        failures.append("missing inquiry source should be represented as an operational unknown")
    krzysztof_data = sample_data(1, False)
    krzysztof_data["client"]["first_name"] = "Krzysztof"
    krzysztof_context = build_offer_context(krzysztof_data, pricing)
    if krzysztof_context["salutation"] != "Dzień dobry Panie Krzysztofie,":
        failures.append(f"Krzysztof salutation should use vocative: {krzysztof_context['salutation']}")
    for first_name, expected in (
        ("Adam", "Dzień dobry Panie Adamie,"),
        ("Ewa", "Dzień dobry Pani Ewo,"),
        ("Natalia", "Dzień dobry Pani Natalio,"),
        ("Alicja", "Dzień dobry Pani Alicjo,"),
        ("Basia", "Dzień dobry Pani Barbaro,"),
        ("Marek", "Dzień dobry Panie Marku,"),
        ("Jacek", "Dzień dobry Panie Jacku,"),
        ("Karol", "Dzień dobry Panie Karolu,"),
        ("Patryk", "Dzień dobry Panie Patryku,"),
        ("Andrzej", "Dzień dobry Panie Andrzeju,"),
        ("Grzegorz", "Dzień dobry Panie Grzegorzu,"),
        ("Jakub", "Dzień dobry Panie Jakubie,"),
        ("Rafał", "Dzień dobry Panie Rafale,"),
        ("Wojciech", "Dzień dobry Panie Wojciechu,"),
    ):
        vocative_data = sample_data(1, False)
        vocative_data["client"]["first_name"] = first_name
        vocative_context = build_offer_context(vocative_data, pricing)
        if vocative_context["salutation"] != expected:
            failures.append(f"{first_name} salutation should use vocative: {vocative_context['salutation']}")
    foreign_name_data = sample_data(1, False)
    foreign_name_data["client"]["first_name"] = "John"
    foreign_name_data["language"] = "pl"
    foreign_name_context = build_offer_context(foreign_name_data, pricing)
    if foreign_name_context["salutation"] != "Dzień dobry Panie John,":
        failures.append(f"foreign-looking name should not be force-declined: {foreign_name_context['salutation']}")

    many_missing = sample_data(1, False)
    many_missing["client"]["company"] = ""
    many_missing["scope"].pop("mailbox_count")
    many_missing["scope"].pop("crm")
    resolved_many_missing, _ = apply_offer_readiness(many_missing)
    questions = missing_questions(validate_required_data(resolved_many_missing), [])
    expected_questions = [QUESTION_BY_FIELD["client.company"]]
    if questions != expected_questions:
        failures.append(f"only true blockers should produce missing-data questions: {questions}")
    if validate_required_data(resolved_many_missing) != ["client.company"]:
        failures.append("missing company should remain the only clarification blocker")

    english_missing = sample_data(2, False)
    english_missing["client"]["first_name"] = "Olivia"
    english_missing["client"]["last_name"] = "Parker"
    english_missing["client"]["company"] = ""
    english_missing["language"] = "en"
    english_missing["scope"].pop("mailbox_count")
    english_missing, _ = apply_offer_readiness(english_missing)
    english_blocked = build_blocked_outputs(english_missing, validate_required_data(english_missing), [])
    if english_blocked["questions"] != [QUESTION_BY_FIELD_EN["client.company"]]:
        failures.append(f"English missing-data questions should be localized: {english_blocked['questions']}")
    if not english_blocked["mail_missing_data"].startswith("Hello Olivia,"):
        failures.append("English missing-data email should use an English salutation")
    english_ready = sample_data(2, False)
    english_ready["client"]["first_name"] = "Olivia"
    english_ready["client"]["last_name"] = "Parker"
    english_ready["client"]["company"] = "Meridian Quote Systems Ultra Test"
    english_ready["language"] = "en"
    english_context = build_offer_context(english_ready, pricing)
    english_final = build_success_outputs(english_context)["mail_final_offer"]
    if not english_final.startswith("Hello Olivia,") or "attaching the Orchesta RFQ implementation offer" not in english_final:
        failures.append(f"English final-offer email should stay English: {english_final!r}")
    if "w załączniku" in english_final or "W razie akceptacji" in english_final:
        failures.append("English final-offer email leaked Polish copy")

    blocked = sample_data(1, False)
    blocked["customer_expectations"]["expects_sms"] = True
    blocks = validate_safety(blocked, DEFAULT_PRICING_PATH)
    if "customer_expects_sms" not in blocks:
        failures.append(f"SMS safety block missing: {blocks}")

    banned_result = validate_output_text(html + "\nSMS", context, page_count=4)
    if banned_result["ok"]:
        failures.append("forbidden text validator did not block SMS")
    company_with_banned_words = deepcopy(context)
    company_with_banned_words["client"]["company"] = "CRM SMS Zoho Control Ultra Test Sp. z o.o."
    allowed_company_text = (
        f"{company_with_banned_words['client']['full_name']}, CRM SMS Zoho Control Ultra Test Sp. z o.o.\n"
        f"{company_with_banned_words['offer']['offer_number']}\n"
        "Wersja v1\n"
        "10 200 zł netto\n"
        "konto pocztowe\n"
        "Płatność: 100% z góry.\n"
        "Wdrożenie: 14 dni.\n"
        "14 dni gwarancji\n"
        f"{FOOTER}\n"
    )
    company_banned_result = validate_output_text(allowed_company_text, company_with_banned_words, page_count=4)
    if not company_banned_result["ok"]:
        failures.append(f"SMS/Zoho inside company name should not block output: {company_banned_result}")

    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        offer_payload = offer_json_payload(context)
        paths = write_obsidian_crm(vault, context, offer_payload)
        for label, raw_path in paths.items():
            if not Path(raw_path).exists():
                failures.append(f"missing obsidian {label}: {raw_path}")
        offer_record = load_json(Path(paths["offer"]))
        if len(offer_record.get("versions", [])) != 1:
            failures.append("offer version record should contain v1")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("rfq_final_offer self-test: ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Orchesta final-offer artifacts without sending email.")
    parser.add_argument("--input", type=Path, help="Input JSON with client, scope, thread, and safety data.")
    parser.add_argument("--output-dir", type=Path, default=Path(".tmp/rfq-final-offer"), help="Artifact output directory.")
    parser.add_argument("--pricing", type=Path, default=DEFAULT_PRICING_PATH, help="Approved pricing JSON path.")
    parser.add_argument("--render-html", action="store_true", help="Kept for readability; HTML is always rendered for complete offers.")
    parser.add_argument("--render-pdf", action="store_true", help="Render PDF with WeasyPrint.")
    parser.add_argument("--allow-missing-weasyprint", action="store_true", help="Return a warning instead of failing when WeasyPrint is unavailable.")
    parser.add_argument("--write-obsidian", action="store_true", help="Write CRM files to the provided Obsidian vault path.")
    parser.add_argument("--vault", type=Path, help="Obsidian vault root for --write-obsidian.")
    parser.add_argument("--delete-pdf-after-success", action="store_true", help="Delete working PDF after success, for draft-attachment runtimes.")
    parser.add_argument("--self-test", action="store_true", help="Run offline deterministic tests.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.input:
        parser.error("--input is required unless --self-test is used")
    try:
        manifest = run(args.input, args.output_dir, args)
    except Exception as exc:
        print(f"rfq_final_offer: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
