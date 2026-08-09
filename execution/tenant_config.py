#!/usr/bin/env python3
"""Tenant configuration loader for multi-tenant Hermes.

Loads per-tenant YAML config from ``tenants/<tenant_id>/``. The single
source of required-data rules is ``<tenant>/offer.yaml`` (spec section 4:
"Jedno źródło danych wymaganych w rozmowie").

The loader is deliberately generic: it reads every ``*.yaml`` file under
``tenants/<tenant_id>/`` so later phases can add ``discovery.yaml``,
``conversation.yaml``, ``notifications.yaml``, ``company.yaml``,
``products.yaml`` etc. without changing this module.

Unknown / unresolved tenants return an empty config; the caller is
responsible for routing such cases to ``awaiting_human`` (spec section 2:
``decision_reason: tenant_not_resolved``).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from offer_readiness import NON_BLOCKING_UNKNOWN_POLICIES, normalize_fact, resolve_readiness

EXECUTION_DIR = Path(__file__).resolve().parent
ROOT_DIR = EXECUTION_DIR.parent
TENANTS_DIR = ROOT_DIR / "tenants"

# Stages referenced by offer.yaml ``required_for`` entries.
KNOWN_STAGES = ("qualification", "final_offer")

# Reason code used when tenant_id cannot be resolved for an inbound message
# (spec section 2: Brak rozpoznanego tenant_id).
REASON_TENANT_NOT_RESOLVED = "tenant_not_resolved"

# Message state used when tenant_id is unresolved (spec section 2).
STATE_AWAITING_HUMAN = "awaiting_human"


def tenant_dir(tenant_id: str) -> Path:
    return TENANTS_DIR / (tenant_id or "").strip()


def tenant_exists(tenant_id: str) -> bool:
    return tenant_dir(tenant_id).is_dir()


def _mailboxes_mapping() -> dict[str, str]:
    """Return the mailbox -> tenant_id mapping from tenants/mailboxes.yaml.

    Keys are lower-cased mailbox addresses; values are tenant_id strings.
    Missing or malformed file -> {}.
    """
    path = TENANTS_DIR / "mailboxes.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    mapping = data.get("mailboxes", {}) if isinstance(data, dict) else {}
    result: dict[str, str] = {}
    for mailbox, entry in (mapping or {}).items():
        if isinstance(entry, dict):
            tenant_id = str(entry.get("tenant_id") or "").strip()
        else:
            tenant_id = str(entry or "").strip()
        if tenant_id:
            result[str(mailbox).strip().lower()] = tenant_id
    return result


def resolve_tenant_id(mailbox: str) -> str:
    """Resolve tenant_id for an inbound mailbox address.

    Returns "" when the mailbox is unknown (caller routes to awaiting_human).
    Never falls back to a default tenant for unknown mailboxes (spec section 2).
    """
    if not mailbox:
        return ""
    entry = _mailboxes_mapping().get(str(mailbox).strip().lower())
    return str(entry or "").strip()


def known_mailboxes() -> list[str]:
    """Return the list of configured mailbox addresses (lower-cased)."""
    return sorted(_mailboxes_mapping().keys())


def mailbox_for_tenant(tenant_id: str) -> str:
    """Return the first configured mailbox for a tenant (convenience)."""
    target = (tenant_id or "").strip()
    for mailbox, tid in _mailboxes_mapping().items():
        if tid == target:
            return mailbox
    return ""


def load_tenant_yaml(tenant_id: str, name: str) -> dict[str, Any]:
    """Load one YAML config file for a tenant. Missing file -> {}."""
    path = tenant_dir(tenant_id) / f"{name}.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return data if isinstance(data, dict) else {}


def load_tenant(tenant_id: str) -> dict[str, Any]:
    """Load all YAML config files for a tenant into a {stem: data} dict."""
    tenant_id = (tenant_id or "").strip()
    if not tenant_id or not tenant_exists(tenant_id):
        return {}
    base = tenant_dir(tenant_id)
    config: dict[str, Any] = {}
    for yaml_path in sorted(base.glob("*.yaml")):
        config[yaml_path.stem] = load_tenant_yaml(tenant_id, yaml_path.stem)
    return config


def offer_fields(tenant_id: str) -> dict[str, dict[str, Any]]:
    """Return the offer.yaml ``fields`` mapping for a tenant.

    This is the single source of required-data rules (spec section 4).
    Shape: {field_name: {"required_for": [...], "customer_visible": bool}}.
    """
    fields = load_tenant_yaml(tenant_id, "offer").get("fields", {})
    return fields if isinstance(fields, dict) else {}


def required_fields_for_stage(tenant_id: str, stage: str) -> list[str]:
    """Return field names required for a given stage (qualification / final_offer)."""
    if stage not in KNOWN_STAGES:
        return []
    result: list[str] = []
    for name, rule in offer_fields(tenant_id).items():
        required_for = (rule or {}).get("required_for", []) or []
        if stage in required_for:
            result.append(name)
    return result


def missing_fields(
    tenant_id: str,
    saved_data: dict[str, Any] | None,
    *,
    stage: str = "final_offer",
) -> list[str]:
    """Return offer.yaml fields still empty for ``stage`` given saved deal facts."""
    facts = saved_data or {}
    missing: list[str] = []
    rules = offer_fields(tenant_id)
    for name in required_fields_for_stage(tenant_id, stage):
        state = normalize_fact(facts.get(name))
        policy = str((rules.get(name) or {}).get("unknown_policy") or "clarify")
        if state.get("state") == "conflicting":
            missing.append(name)
        elif state.get("state") not in {"known", "assumed"} and policy not in NON_BLOCKING_UNKNOWN_POLICIES:
            missing.append(name)
    return missing


def resolve_offer_readiness(
    tenant_id: str,
    saved_data: dict[str, Any] | None,
    *,
    stage: str = "final_offer",
) -> dict[str, Any]:
    """Return deterministic readiness plus approved assumptions for an offer."""
    return resolve_readiness(saved_data or {}, offer_fields(tenant_id), stage=stage)


def customer_visible_fields(tenant_id: str, stage: str | None = None) -> list[str]:
    """Return field names marked customer_visible, optionally for a stage."""
    result: list[str] = []
    for name, rule in offer_fields(tenant_id).items():
        if not (rule or {}).get("customer_visible", False):
            continue
        if stage is None or stage in (rule.get("required_for", []) or []):
            result.append(name)
    return result


def field_rule(tenant_id: str, field_name: str) -> dict[str, Any]:
    """Return the rule for a single field, or {} if undefined."""
    return offer_fields(tenant_id).get(field_name, {}) or {}


def is_field_required_for(tenant_id: str, field_name: str, stage: str) -> bool:
    """True if field_name is required for the given stage."""
    return stage in field_rule(tenant_id, field_name).get("required_for", []) or []


# Per-tenant YAML files that make up a tenant package (spec section 2).
TENANT_PACKAGE_FILES = (
    "company",
    "products",
    "discovery",
    "offer",
    "conversation",
    "notifications",
)


def load_company(tenant_id: str) -> dict[str, Any]:
    return load_tenant_yaml(tenant_id, "company")


def load_products(tenant_id: str) -> dict[str, Any]:
    return load_tenant_yaml(tenant_id, "products")


def load_discovery(tenant_id: str) -> dict[str, Any]:
    return load_tenant_yaml(tenant_id, "discovery")


def load_conversation(tenant_id: str) -> dict[str, Any]:
    return load_tenant_yaml(tenant_id, "conversation")


def load_notifications(tenant_id: str) -> dict[str, Any]:
    """Internal notifications config. Never passed to the reply writer."""
    return load_tenant_yaml(tenant_id, "notifications")


def internal_notification_email(tenant_id: str, default: str = "notifications@example.invalid") -> str:
    """Return the tenant's internal email recipient, never customer-facing."""
    internal = load_notifications(tenant_id).get("internal_notifications", {}) or {}
    email = internal.get("email", {}) if isinstance(internal, dict) else {}
    recipient = str((email or {}).get("recipient") or "").strip().lower()
    return recipient or default


def load_commercial_policy(tenant_id: str) -> dict[str, Any]:
    """Load optional tenant-specific commercial exception rules."""
    return load_tenant_yaml(tenant_id, "commercial_policy")


def commercial_exceptions(tenant_id: str, text: str) -> list[str]:
    """Return configured commercial exception codes found in customer text."""
    policy = load_commercial_policy(tenant_id)
    rules = policy.get("manual_review", {}) if isinstance(policy, dict) else {}
    matches: list[str] = []
    for code, rule in (rules or {}).items():
        patterns = (rule or {}).get("patterns", []) if isinstance(rule, dict) else []
        if any(re.search(str(pattern), text or "", flags=re.IGNORECASE | re.DOTALL) for pattern in patterns):
            matches.append(str(code))
    return matches


def discovery_questions(tenant_id: str, field_name: str, language: str = "pl") -> str:
    """Return the discovery question text for a field in a given language."""
    questions = load_discovery(tenant_id).get("questions", {}) or {}
    entry = questions.get(field_name, {}) or {}
    return str(entry.get(language, entry.get("pl", "")) or "")


def validate_tenant_package(tenant_id: str) -> list[str]:
    """Return a list of missing required files for a tenant package (empty = ok)."""
    missing: list[str] = []
    if not tenant_exists(tenant_id):
        return [f"tenant directory missing: tenants/{tenant_id}"]
    required_files = TENANT_PACKAGE_FILES + (("commercial_policy",) if tenant_id == "orchesta" else ())
    for name in required_files:
        if not (tenant_dir(tenant_id) / f"{name}.yaml").exists():
            missing.append(f"{name}.yaml")
    return missing


def self_test() -> int:
    failures: list[str] = []

    if not tenant_exists("orchesta"):
        failures.append("orchesta tenant directory missing")
    else:
        fields = offer_fields("orchesta")
        if not fields:
            failures.append("orchesta offer.yaml has no fields")
        for stage in KNOWN_STAGES:
            names = required_fields_for_stage("orchesta", stage)
            if not names:
                failures.append(f"orchesta has no required fields for stage {stage}")
            for name in names:
                if name not in fields:
                    failures.append(f"required field {name} not in offer.yaml fields")

    if offer_fields("nonexistent_tenant"):
        failures.append("unknown tenant should return empty offer fields")
    if required_fields_for_stage("orchesta", "bogus_stage"):
        failures.append("unknown stage should return no fields")
    if not is_field_required_for("orchesta", "mailbox_count", "final_offer"):
        failures.append("mailbox_count should be required for final_offer")
    if is_field_required_for("orchesta", "current_process", "final_offer"):
        failures.append("current_process should NOT be required for final_offer")
    if not customer_visible_fields("orchesta", "final_offer"):
        failures.append("orchesta final_offer should have customer_visible fields")
    readiness = resolve_offer_readiness(
        "orchesta",
        {
            "company_name_or_website": "Example Sp. z o.o.",
            "mailbox_count": {"state": "unknown_confirmed"},
            "crm": {"state": "unknown_confirmed"},
        },
    )
    if readiness.get("status") != "ready_with_assumptions":
        failures.append("unknown mailbox/CRM should resolve to an explicit start variant")

    # tenant_id resolution (spec section 3)
    if resolve_tenant_id("rfq-mailbox@example.invalid") != "orchesta":
        failures.append("rfq-mailbox@example.invalid should resolve to orchesta")
    if resolve_tenant_id("identity-004@customer-004.example.com") != "orchesta":
        failures.append("resolver should be case-insensitive")
    if resolve_tenant_id("unknown@nowhere.test") != "":
        failures.append("unknown mailbox should resolve to empty (no default)")
    if mailbox_for_tenant("orchesta") != "rfq-mailbox@example.invalid":
        failures.append("mailbox_for_tenant(orchesta) should be rfq-mailbox@example.invalid")
    if "rfq-mailbox@example.invalid" not in known_mailboxes():
        failures.append("known_mailboxes should list rfq-mailbox@example.invalid")

    # Tenant package completeness (spec section 2)
    for tenant in ("orchesta", "firma_abc"):
        missing = validate_tenant_package(tenant)
        if missing:
            failures.append(f"tenant {tenant} missing files: {missing}")
        else:
            # Smoke-check the loaders return non-empty dicts.
            if not load_company(tenant).get("company"):
                failures.append(f"{tenant} company.yaml missing company block")
            if not load_products(tenant).get("pricing_id"):
                failures.append(f"{tenant} products.yaml missing pricing_id")
            if not load_discovery(tenant).get("questions"):
                failures.append(f"{tenant} discovery.yaml missing questions")
            if not load_conversation(tenant).get("language"):
                failures.append(f"{tenant} conversation.yaml missing language")
            if not load_notifications(tenant).get("internal_notifications"):
                failures.append(f"{tenant} notifications.yaml missing internal_notifications")
    if not load_commercial_policy("orchesta").get("manual_review"):
        failures.append("orchesta commercial_policy.yaml missing manual_review rules")
    if internal_notification_email("orchesta") != "identity-004@gmail.com":
        failures.append("orchesta internal notification email is wrong")
    for sample, expected in (
        ("Czy możemy dostać rabat?", "discount"),
        ("Poproszę wycenę w EUR.", "foreign_currency"),
        ("Płatność etapami.", "installment_payment"),
        ("Potrzebujemy indywidualnego SLA.", "custom_sla"),
    ):
        if expected not in commercial_exceptions("orchesta", sample):
            failures.append(f"commercial exception not detected: {expected}")
    # discovery_questions lookup
    if not discovery_questions("orchesta", "mailbox_count", "pl"):
        failures.append("orchesta discovery question for mailbox_count missing")
    if discovery_questions("orchesta", "mailbox_count", "en") != "How many inboxes should the system monitor?":
        failures.append("orchesta EN discovery question for mailbox_count wrong")

    if failures:
        for failure in failures:
            print(f"tenant_config self-test FAIL: {failure}")
        return 1
    print("tenant_config self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
