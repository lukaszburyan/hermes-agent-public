#!/usr/bin/env python3
"""Versioned contract shared by reply prompting, validation and release checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReplyContract:
    version: str
    target_words_min: int
    target_words_max: int
    hard_words_max: int
    max_questions: int
    forbidden_characters: tuple[str, ...]
    forbidden_terms: tuple[str, ...]
    supported_languages: tuple[str, ...]
    opening_policy: str
    follow_up_greeting_policy: str
    saved_fields: tuple[str, ...]
    message_type_rules: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        payload = json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def prompt_fragment(self) -> str:
        rules = "\n".join(f"- {message_type}: {rule}" for message_type, rule in self.message_type_rules)
        return (
            "\n\n## Authoritative ReplyContract\n"
            f"contract_version: {self.version}\n"
            f"contract_digest: {self.digest}\n"
            f"supported_languages: {', '.join(self.supported_languages)}\n"
            f"target_words: {self.target_words_min}-{self.target_words_max}; hard_max: {self.hard_words_max}\n"
            f"max_questions: {self.max_questions}\n"
            f"opening_policy: {self.opening_policy}\n"
            f"follow_up_greeting_policy: {self.follow_up_greeting_policy}\n"
            f"forbidden_characters: {json.dumps(self.forbidden_characters, ensure_ascii=False)}\n"
            f"forbidden_terms: {json.dumps(self.forbidden_terms, ensure_ascii=False)}\n"
            f"saved_fields: {', '.join(self.saved_fields)}\n"
            "message_type_rules:\n"
            f"{rules}\n"
            "Return contract_version and message_language in the JSON output."
        )


DEFAULT_REPLY_CONTRACT = ReplyContract(
    version="reply-contract-v1",
    target_words_min=40,
    target_words_max=100,
    hard_words_max=120,
    max_questions=2,
    forbidden_characters=("--", "–", "—"),
    forbidden_terms=(
        "telegram", "deal_id", "routing", "classifier", "prompt", "llm", "poller",
        "manual_review", "human_takeover", "conversation_handoff", "discovery_dead_end",
        "ready_for_final_offer", "skip_pre_offer_send", "commercial_exception",
        "wewnętrzne powiadomienie", "wewnetrzne powiadomienie", "automatyzacja rozmowy",
        "automatyczna rozmowa", "hermes",
    ),
    supported_languages=("pl", "en", "de"),
    opening_policy="first reply uses one full greeting and at most one standard thank-you",
    follow_up_greeting_policy="follow-up has no full greeting and no repeated standard thank-you",
    saved_fields=(
        "company_name_or_website", "current_process", "inquiry_channels", "monthly_volume",
        "mailbox_count", "crm", "has_sample_requests",
    ),
    message_type_rules=(
        ("acknowledgement", "operational; may auto-send only after full validation"),
        ("clarification_request", "operational; asks at most two unresolved questions"),
        ("missing_data_request", "operational; asks at most two missing fields"),
        ("follow_up", "operational; never re-greets or repeats standard thanks"),
        ("ready_for_offer_notice", "operational; contains no commercial terms"),
        ("final_offer", "draft_only in every language; never auto-send"),
    ),
)


REPLY_CONTRACT_VERSION = DEFAULT_REPLY_CONTRACT.version
REPLY_CONTRACT_DIGEST = DEFAULT_REPLY_CONTRACT.digest
