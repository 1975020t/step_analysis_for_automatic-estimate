from src.chat_service import ChatQuoteService
import json

import pytest

from src.llm_client import MockLLMClient, OpenAILLMClient, build_llm_client
from src.master_loader import MasterLoader
from src.models import ChatInterpretation, QuoteCondition, QuoteOperation


def service() -> ChatQuoteService:
    return ChatQuoteService(MockLLMClient(), MasterLoader("data"))


def test_countersink_quantity():
    result = service().interpret_and_apply(
        "皿もみ2つ追加して", QuoteCondition(material="AL5052", quantity=10)
    )
    item = result.condition.additional_processes[0]
    assert item.process_code == "COUNTERSINK"
    assert item.quantity == 2


def test_ambiguous_buff_needs_confirmation():
    result = service().interpret_and_apply(
        "バフもお願いします", QuoteCondition(material="AL5052", quantity=10)
    )
    assert result.interpretation.status == "needs_confirmation"
    assert "#400" in result.message


def test_unambiguous_operation_is_applied_while_buff_waits_for_confirmation():
    result = service().interpret_and_apply(
        "皿もみ2つとバフもお願いします", QuoteCondition(material="AL5052", quantity=10)
    )
    assert result.interpretation.status == "needs_confirmation"
    assert result.condition.additional_processes[0].process_code == "COUNTERSINK"
    assert result.condition.additional_processes[0].quantity == 2


def test_400_confirms_buff():
    result = service().interpret_and_apply(
        "400番で", QuoteCondition(material="AL5052", quantity=10)
    )
    assert result.condition.additional_processes[0].process_code == "BUFF_400"


def test_unknown_process_is_not_added():
    result = service().interpret_and_apply(
        "特殊な焼け取りを追加", QuoteCondition(material="AL5052", quantity=10)
    )
    assert result.interpretation.status == "unknown"
    assert result.condition.additional_processes == []


def test_quantity_and_material_changes():
    quantity = service().interpret_and_apply(
        "数量50個なら？", QuoteCondition(material="SUS304", quantity=10)
    )
    assert quantity.condition.quantity == 50
    material = service().interpret_and_apply("AL5052で", quantity.condition)
    assert material.condition.material == "AL5052"


def test_empty_process_from_confirmation_is_safe_to_parse_and_ignore():
    parsed = ChatInterpretation(
        status="needs_confirmation",
        operations=[QuoteOperation(action="add", field="process", process_code=None)],
        confirmation_message="工程を確認してください",
    )
    assert parsed.operations[0].process_code is None


def test_quantity_operation_accepts_quantity_field_used_by_structured_output():
    class QuantityFieldClient:
        def interpret_quote_change(self, **_kwargs):
            return ChatInterpretation(
                status="ready",
                operations=[
                    QuoteOperation(
                        action="update", field="quantity", quantity=50, value=None
                    )
                ],
            )

    result = ChatQuoteService(QuantityFieldClient(), MasterLoader("data")).interpret_and_apply(
        "数量50個", QuoteCondition(material="AL5052", quantity=10)
    )
    assert result.condition.quantity == 50


def test_openai_payload_is_geometry_and_price_free():
    captured = {}

    class Responses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"output_parsed": ChatInterpretation(status="unknown")})()

    client = OpenAILLMClient.__new__(OpenAILLMClient)
    client.client = type("Client", (), {"responses": Responses()})()
    client.model = "test-model"
    client.interpret_quote_change(
        user_text="数量を2個にして",
        condition=QuoteCondition(material="AL5052", quantity=1),
        process_catalog=[{
            "process_code": "COUNTERSINK", "display_name": "皿穴", "unit_price": 999,
            "aliases": ["皿もみ"], "unit": "hole",
        }],
        chat_history=[{"role": "user", "content": "前の条件"}],
    )
    payload = json.loads(captured["input"][1]["content"])
    assert set(payload) == {
        "current_condition", "material_codes", "process_codes",
        "pending_confirmation", "recent_chat_text", "user_text",
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "unit_price" not in serialized
    assert "display_name" not in serialized
    assert ".step" not in serialized.lower()


def test_openai_mode_without_key_does_not_fall_back_to_mock(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        build_llm_client()


def test_api_failure_does_not_mutate_existing_condition():
    class FailingClient:
        def interpret_quote_change(self, **_kwargs):
            raise RuntimeError("temporary API failure")

    original = QuoteCondition(material="AL5052", quantity=10)
    with pytest.raises(RuntimeError, match="temporary API failure"):
        ChatQuoteService(FailingClient(), MasterLoader("data")).interpret_and_apply(
            "数量を50個にして", original
        )
    assert original.material == "AL5052"
    assert original.quantity == 10
    assert original.additional_processes == []
