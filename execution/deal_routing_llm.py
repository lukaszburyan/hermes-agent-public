#!/usr/bin/env python3
"""Controlled LLM adapter for genuinely ambiguous same-customer deal routing."""

from __future__ import annotations

import json
from typing import Any, Callable

from llm_intent_classifier import GatewayLLMClient


SYSTEM_PROMPT = """Decydujesz wyłącznie, do której aktywnej sprawy należy wiadomość.
Nie twórz odpowiedzi dla klienta i nie zmieniaj żadnych danych sprawy.
Zwróć wyłącznie JSON z dokładnie czterema polami:
decision (existing, new albo manual_review), deal_id, confidence, reason.
Dla existing wybierz deal_id wyłącznie z przekazanej listy. Dla new i
manual_review deal_id musi być pustym tekstem. Jeśli nie masz bezpiecznej
pewności, wybierz manual_review.
"""

ROUTING_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["existing", "new", "manual_review"]},
        "deal_id": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["decision", "deal_id", "confidence", "reason"],
    "additionalProperties": False,
}


def build_gateway_router(client: Any | None = None) -> Callable[[dict[str, Any]], str]:
    """Return a lazy callable; no model call happens until routing is ambiguous."""
    gateway = client or GatewayLLMClient()

    def route(payload: dict[str, Any]) -> str:
        user_prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if hasattr(gateway, "complete_structured"):
            return gateway.complete_structured(
                SYSTEM_PROMPT,
                user_prompt,
                response_schema=ROUTING_SCHEMA,
                schema_name="deal_routing_decision",
                mode="CLASSIFY",
                temperature=0.0,
            )
        return gateway.complete(SYSTEM_PROMPT, user_prompt, temperature=0.0)

    return route
