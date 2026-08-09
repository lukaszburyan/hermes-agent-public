#!/usr/bin/env python3
"""Conservative deterministic cleanup for style-only reply defects."""

from __future__ import annotations

import re
from typing import Any


_LEADING_GREETINGS = re.compile(
    r"^\s*(?:dzień\s+dobry(?:\s+[^,\n]+)?|good\s+(?:morning|afternoon|evening)|hello(?:\s+[^,\n]+)?|"
    r"guten\s+(?:morgen|tag|abend)|hallo(?:\s+[^,\n]+)?)\s*[,!]?\s*\n*",
    re.IGNORECASE,
)
_STANDARD_THANKS = re.compile(
    r"^\s*(?:dziękuję|dziekuje)\s+za\s+wiadomość\.?\s*|"
    r"^\s*thank\s+you\s+for\s+(?:your\s+)?message\.?\s*|"
    r"^\s*vielen\s+dank\s+für\s+(?:ihre\s+)?nachricht\.?\s*",
    re.IGNORECASE,
)


def _decorative_symbol(char: str) -> bool:
    code = ord(char)
    return 0x1F300 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF


def sanitize_reply(body: str, *, is_first_agent_reply: bool) -> dict[str, Any]:
    """Repair only punctuation, decorative symbols and repeated boilerplate.

    Numbers and ordinary words are never rewritten, so business meaning is not
    silently changed. The caller must run the complete validator afterwards.
    """
    original = str(body or "")
    value = original
    actions: list[str] = []
    offending_spans: list[dict[str, Any]] = []

    for token, replacement, action in (("—", ",", "em_dash_removed"), ("–", ",", "en_dash_removed"), ("--", ",", "double_hyphen_removed")):
        if token in value:
            offending_spans.extend({"code": action, "text": token, "start": match.start(), "end": match.end()} for match in re.finditer(re.escape(token), value))
            value = value.replace(token, replacement)
            actions.append(action)

    filtered = "".join(char for char in value if not _decorative_symbol(char))
    if filtered != value:
        value = filtered
        actions.append("decorative_emoji_removed")

    if not is_first_agent_reply:
        without_greeting = _LEADING_GREETINGS.sub("", value, count=1)
        if without_greeting != value:
            value = without_greeting
            actions.append("followup_greeting_removed")
        without_thanks = _STANDARD_THANKS.sub("", value, count=1)
        if without_thanks != value:
            value = without_thanks
            actions.append("repeated_thanks_removed")
    else:
        matches = list(_STANDARD_THANKS.finditer(value))
        if len(matches) > 1:
            first = matches[0]
            tail = _STANDARD_THANKS.sub("", value[first.end():])
            value = value[:first.end()] + tail
            actions.append("duplicate_thanks_removed")

    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    return {
        "body": value,
        "changed": value != original,
        "actions": list(dict.fromkeys(actions)),
        "offending_spans": offending_spans,
    }
