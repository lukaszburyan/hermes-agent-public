#!/usr/bin/env python3
"""Conversation continuity for a deal (spec section 13).

The script owns the counters and the ``is_first_agent_reply`` flag; the LLM
never guesses whether it is the first reply. This module holds the pure
state-transition logic plus new-thread detection (headers + deal id, never
time alone).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

EXECUTION_DIR = Path(__file__).resolve().parent
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

STAGES = ("discovery", "qualification", "final_offer")


def default_state(deal_id: str = "") -> dict[str, Any]:
    return {
        "deal_id": str(deal_id),
        "agent_reply_count": 0,
        "customer_message_count": 0,
        "is_first_agent_reply": True,
        "acknowledgement_already_sent": False,
        "conversation_stage": "discovery",
        "last_agent_reply_at": None,
        "last_customer_reply_at": None,
    }


def derive_is_first_agent_reply(agent_reply_count: int) -> bool:
    """Spec section 13: agent_reply_count == 0 -> first reply; >0 -> not first."""
    return int(agent_reply_count) == 0


def on_customer_message(state: dict[str, Any] | None, *, occurred_at: str = "") -> dict[str, Any]:
    """Record an inbound customer message: bump customer_message_count + timestamp."""
    state = dict(state or default_state())
    state["customer_message_count"] = int(state.get("customer_message_count", 0)) + 1
    if occurred_at:
        state["last_customer_reply_at"] = occurred_at
    # is_first_agent_reply is derived purely from agent_reply_count.
    state["is_first_agent_reply"] = derive_is_first_agent_reply(state.get("agent_reply_count", 0))
    return state


def on_agent_reply(state: dict[str, Any] | None, *, occurred_at: str = "") -> dict[str, Any]:
    """Record an outbound agent reply (spec section 13 first-reply post-conditions).

    After the first reply: agent_reply_count=1, acknowledgement_already_sent=true,
    is_first_agent_reply=false, state=awaiting_customer (caller sets the deal status).
    """
    state = dict(state or default_state())
    state["agent_reply_count"] = int(state.get("agent_reply_count", 0)) + 1
    state["is_first_agent_reply"] = False
    state["acknowledgement_already_sent"] = True
    if occurred_at:
        state["last_agent_reply_at"] = occurred_at
    return state


def is_new_thread(
    *,
    has_prior_thread_headers: bool,
    same_deal_id: bool,
    same_topic: bool,
    previous_deal_closed: bool,
) -> bool:
    """Decide whether a new message from the same sender starts a new thread.

    Spec section 13: a new message may be a new conversation if it has no
    earlier thread headers, a different deal id, a different topic, or the
    previous deal is closed. Time alone is never the criterion.

    A message continues the SAME thread only when ALL of these hold:
    it carries prior thread headers, matches the existing deal id, shares the
    topic, and the previous deal is still open. Otherwise it is a new thread.
    """
    same_thread = (
        has_prior_thread_headers
        and same_deal_id
        and same_topic
        and not previous_deal_closed
    )
    return not same_thread


def conversation_stage_for(
    *,
    missing_qualification_fields: list[str],
    missing_final_offer_fields: list[str],
) -> str:
    """Derive the conversation stage from what is still missing.

    discovery -> still asking qualification questions
    qualification -> qualification complete, still missing final-offer data
    final_offer -> everything needed for an offer is present
    """
    if missing_qualification_fields:
        return "discovery"
    if missing_final_offer_fields:
        return "qualification"
    return "final_offer"


def self_test() -> int:
    failures: list[str] = []
    s0 = default_state("deal-1")
    if not s0["is_first_agent_reply"]:
        failures.append("default state should be first agent reply")
    if derive_is_first_agent_reply(0) is not True:
        failures.append("agent_reply_count 0 -> first reply")
    if derive_is_first_agent_reply(3) is not False:
        failures.append("agent_reply_count >0 -> not first reply")

    s1 = on_customer_message(s0, occurred_at="2026-08-03T11:30:00+02:00")
    if s1["customer_message_count"] != 1:
        failures.append("on_customer_message should bump customer_message_count")
    if s1["is_first_agent_reply"] is not True:
        failures.append("before any agent reply, is_first_agent_reply stays True")
    if s1["last_customer_reply_at"] != "2026-08-03T11:30:00+02:00":
        failures.append("last_customer_reply_at not set")

    s2 = on_agent_reply(s1, occurred_at="2026-08-03T11:31:00+02:00")
    if s2["agent_reply_count"] != 1:
        failures.append("on_agent_reply should bump agent_reply_count")
    if s2["is_first_agent_reply"] is not False:
        failures.append("after first agent reply, is_first_agent_reply must be False")
    if s2["acknowledgement_already_sent"] is not True:
        failures.append("after first agent reply, acknowledgement_already_sent must be True")

    # A second customer message must NOT flip is_first_agent_reply back to True.
    s3 = on_customer_message(s2, occurred_at="2026-08-03T12:00:00+02:00")
    if s3["is_first_agent_reply"] is not False:
        failures.append("subsequent customer message must keep is_first_agent_reply False")
    if s3["customer_message_count"] != 2:
        failures.append("second customer message should bump customer_message_count to 2")

    # New-thread detection
    if not is_new_thread(
        has_prior_thread_headers=False, same_deal_id=True, same_topic=True, previous_deal_closed=False
    ):
        failures.append("no prior headers -> new thread")
    if is_new_thread(
        has_prior_thread_headers=True, same_deal_id=True, same_topic=True, previous_deal_closed=False
    ):
        failures.append("prior headers + same deal -> same thread")
    if not is_new_thread(
        has_prior_thread_headers=True, same_deal_id=False, same_topic=True, previous_deal_closed=False
    ):
        failures.append("prior headers but different deal -> new thread")
    if not is_new_thread(
        has_prior_thread_headers=True, same_deal_id=True, same_topic=True, previous_deal_closed=True
    ):
        failures.append("prior headers + closed previous deal -> new thread")

    # Stage derivation
    if conversation_stage_for(missing_qualification_fields=["x"], missing_final_offer_fields=[]) != "discovery":
        failures.append("missing qualification -> discovery")
    if conversation_stage_for(missing_qualification_fields=[], missing_final_offer_fields=["y"]) != "qualification":
        failures.append("missing final-offer data -> qualification")
    if conversation_stage_for(missing_qualification_fields=[], missing_final_offer_fields=[]) != "final_offer":
        failures.append("nothing missing -> final_offer")

    if failures:
        for failure in failures:
            print(f"conversation_continuity self-test FAIL: {failure}")
        return 1
    print("conversation_continuity self-test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
