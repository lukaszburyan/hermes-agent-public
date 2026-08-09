#!/usr/bin/env python3
"""Deterministic offer readiness for known, unknown and assumed customer facts."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


FACT_STATES = frozenset({"known", "unknown_confirmed", "assumed", "conflicting", "not_asked"})
NON_BLOCKING_UNKNOWN_POLICIES = frozenset({"assume", "optional_variant", "optional"})


def normalize_fact(value: Any, *, absent_state: str = "not_asked") -> dict[str, Any]:
    """Return the canonical fact envelope without turning unknown into false."""
    if isinstance(value, dict) and "state" in value:
        raw_state = str(value.get("state") or absent_state).strip().lower()
        aliases = {
            "yes": "known",
            "no": "known",
            "unknown": "unknown_confirmed",
            "missing": absent_state,
        }
        state = aliases.get(raw_state, raw_state)
        if state not in FACT_STATES:
            state = absent_state
        result = dict(value)
        result["state"] = state
        if raw_state == "yes":
            result["value"] = True
        elif raw_state == "no":
            result["value"] = False
        if state != "known" and state != "assumed":
            result.pop("value", None)
        return result
    if value is None or value == "" or value == {}:
        return {"state": absent_state}
    return {"state": "known", "value": value}


def resolve_readiness(
    facts: dict[str, Any] | None,
    rules: dict[str, dict[str, Any]] | None,
    *,
    stage: str = "final_offer",
) -> dict[str, Any]:
    """Resolve exact/assumed/clarification/blocked readiness from field rules."""
    source = facts or {}
    field_rules = rules or {}
    states: dict[str, dict[str, Any]] = {}
    resolved: dict[str, Any] = {}
    assumptions: list[dict[str, Any]] = []
    blocking: list[str] = []
    clarification: list[str] = []

    for field, rule in field_rules.items():
        rule = rule or {}
        state = normalize_fact(source.get(field))
        states[field] = state
        if state["state"] == "known":
            resolved[field] = state.get("value")
            continue
        if state["state"] == "assumed":
            resolved[field] = state.get("value")
            assumptions.append({"field": field, "value": state.get("value"), "label": rule.get("assumption_label", "")})
            continue
        if state["state"] == "conflicting":
            blocking.append(field)
            continue

        required = stage in (rule.get("required_for") or [])
        policy = str(rule.get("unknown_policy") or ("clarify" if required else "optional"))
        if policy in {"assume", "optional_variant"}:
            assumed = {
                "state": "assumed",
                "value": deepcopy(rule.get("default_value")),
                "basis": str(rule.get("assumption_label") or "zatwierdzony wariant startowy"),
                "original_state": state["state"],
            }
            states[field] = assumed
            resolved[field] = assumed["value"]
            assumptions.append({
                "field": field,
                "value": assumed["value"],
                "label": assumed["basis"],
                "variant": deepcopy(rule.get("variant") or {}),
            })
        elif policy == "optional":
            continue
        elif required:
            clarification.append(field)

    status = "ready_exact"
    if blocking:
        status = "blocked"
    elif clarification:
        status = "needs_clarification"
    elif assumptions:
        status = "ready_with_assumptions"

    return {
        "status": status,
        "resolved_facts": resolved,
        "fact_states": states,
        "assumptions": assumptions,
        "blocking_fields": blocking,
        "clarification_fields": clarification,
    }


DEFAULT_FINAL_OFFER_RULES: dict[str, dict[str, Any]] = {
    "mailbox_count": {
        "required_for": ["final_offer"],
        "unknown_policy": "assume",
        "default_value": 1,
        "assumption_label": "wariant startowy dla jednego konta pocztowego",
    },
    "crm": {
        "required_for": ["final_offer"],
        "unknown_policy": "optional_variant",
        "default_value": False,
        "assumption_label": "CRM jako opcja do potwierdzenia",
        "variant": {"value": True, "label": "integracja CRM jako opcja dodatkowa"},
    },
    "monthly_volume": {"required_for": [], "unknown_policy": "optional"},
    "current_process": {"required_for": [], "unknown_policy": "optional"},
    "inquiry_channels": {"required_for": [], "unknown_policy": "optional"},
}


def resolve_final_offer_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve the two approved pricing assumptions for the final-offer helper."""
    raw = dict(scope or {})
    for field in ("mailbox_count", "crm", "monthly_volume"):
        state_key = f"{field}_state"
        if field not in raw and raw.get(state_key):
            raw[field] = {"state": raw[state_key]}
    return resolve_readiness(raw, DEFAULT_FINAL_OFFER_RULES, stage="final_offer")
