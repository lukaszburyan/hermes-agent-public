from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
EXECUTION = ROOT / "execution"
if str(EXECUTION) not in sys.path:
    sys.path.insert(0, str(EXECUTION))

import deal_routing_llm  # noqa: E402
import llm_intent_classifier as classifier  # noqa: E402


def test_gateway_client_prefers_hermes_bridge_and_passes_exact_schema(tmp_path: Path, monkeypatch):
    bridge = tmp_path / "hermes_llm_bridge.py"
    bridge.write_text("# bridge fixture\n", encoding="utf-8")
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["request"] = json.loads(kwargs["input"])
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"result": '{"status":"ok"}', "provider": "openai-codex", "model": "gpt-5.6-luna"}),
            stderr="",
        )

    monkeypatch.setattr(classifier.subprocess, "run", fake_run)
    client = classifier.GatewayLLMClient(
        bridge_path=bridge,
        python_path=Path(sys.executable),
        env={"HERMES_LLM_TIMEOUT_SECONDS": "10"},
    )
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["ok"]}},
        "required": ["status"],
        "additionalProperties": False,
    }
    result = client.complete_structured(
        "system",
        '{"message":"synthetic"}',
        response_schema=schema,
        schema_name="health_check",
        mode="CLASSIFY",
    )

    assert result == '{"status":"ok"}'
    assert client.transport == "hermes"
    assert captured["command"] == [sys.executable, str(bridge)]
    assert captured["request"]["input"] == {"message": "synthetic"}
    assert captured["request"]["response_schema"] == schema
    assert captured["request"]["schema_name"] == "health_check"


def test_deal_router_requests_only_the_approved_four_field_schema():
    captured: dict = {}

    class StructuredSpy:
        def complete_structured(self, system_prompt, user_prompt, **kwargs):
            captured.update(kwargs)
            captured["payload"] = json.loads(user_prompt)
            return '{"decision":"new","deal_id":"","confidence":0.91,"reason":"nowy temat"}'

    result = deal_routing_llm.build_gateway_router(StructuredSpy())({"subject": "Nowy temat"})
    assert json.loads(result)["decision"] == "new"
    assert captured["mode"] == "CLASSIFY"
    assert captured["schema_name"] == "deal_routing_decision"
    assert set(captured["response_schema"]["properties"]) == {"decision", "deal_id", "confidence", "reason"}
    assert captured["response_schema"]["additionalProperties"] is False
    assert captured["payload"] == {"subject": "Nowy temat"}


def test_explicit_command_transport_remains_available_for_local_codex(monkeypatch):
    monkeypatch.setattr(
        classifier.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr=""),
    )
    client = classifier.GatewayLLMClient(command=["codex-fixture"], env={})
    assert client.transport == "command"
    assert client.complete("system", "user") == '{"ok":true}'
