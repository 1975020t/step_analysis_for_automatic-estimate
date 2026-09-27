"""Claude API scaffolding tests. None of these call the network."""
from __future__ import annotations

import pytest

from src.claude_api import ClaudeCacheMiss, ClaudeClient, ClaudeConfigError
from src.llm_assisted_analyzer import LLMAssistedAnalyzer
from src.models import SheetMetalAnalysis

SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]}


def _tool_response(payload):
    return {"content": [{"type": "tool_use", "name": "report", "input": payload}],
            "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_record_mode_calls_once_then_reuses_cache(tmp_path, monkeypatch):
    client = ClaudeClient(model="test-model", mode="record", cache_dir=tmp_path, api_key="dummy")
    calls = []
    monkeypatch.setattr(client, "_live", lambda request: calls.append(request) or _tool_response({"verdict": "accept"}))

    assert client.complete_json("sys", "user", SCHEMA) == {"verdict": "accept"}
    assert client.complete_json("sys", "user", SCHEMA) == {"verdict": "accept"}
    assert len(calls) == 1
    assert client.usage.cache_hits == 1
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_replay_mode_never_calls_the_api(tmp_path):
    client = ClaudeClient(model="test-model", mode="replay", cache_dir=tmp_path, api_key="")
    with pytest.raises(ClaudeCacheMiss):
        client.complete_json("sys", "user", SCHEMA)


def test_live_call_without_key_is_a_clear_configuration_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = ClaudeClient(model="test-model", mode="live", cache_dir=tmp_path, api_key="")
    with pytest.raises(ClaudeConfigError):
        client.complete_json("sys", "user", SCHEMA)


def _result(status="success"):
    return SheetMetalAnalysis(status=status, file_name="x.step", thickness_mm=2.0, blank_area_mm2=100.0,
                              cut_length_mm=40.0, hole_count=0, bend_count=1)


def test_llm_review_can_only_make_results_more_cautious():
    accepted = LLMAssistedAnalyzer.apply(_result("partial"), {"verdict": "accept"})
    assert accepted.status == "partial"  # never upgraded

    downgraded = LLMAssistedAnalyzer.apply(_result(), {"verdict": "downgrade", "reason": "r"})
    assert downgraded.status == "partial"
    assert "LLM_REVIEW_FLAGGED" in downgraded.reason_codes
    assert downgraded.blank_area_mm2 == 100.0  # numbers untouched

    rejected = LLMAssistedAnalyzer.apply(_result(), {"verdict": "reject", "reason": "r"})
    assert rejected.status == "unsupported"
    assert rejected.reason_code == "LLM_REVIEW_REJECTED"


def test_llm_assisted_analyzer_runs_end_to_end_with_a_fake_client(tmp_path):
    cq = pytest.importorskip("cadquery")
    shape = cq.Workplane("XY").box(100, 50, 2.0).faces(">Z").workplane().hole(10).val()
    path = tmp_path / "plate.step"
    cq.exporters.export(shape, str(path))

    client = ClaudeClient(model="test-model", mode="record", cache_dir=tmp_path / "cache", api_key="dummy")
    client._live = lambda request: _tool_response({"verdict": "downgrade", "part_type": "平板",
                                                   "concerns": [], "reason": "test"})
    analyzer = LLMAssistedAnalyzer(k_factor=0.5, k_factor_is_default=False, client=client)
    result = analyzer.analyze(path)

    assert result.status == "partial"
    assert result.hole_count == 1
    assert analyzer.last_usage["llm_cache_hits"] == 0
    assert analyzer.last_review["part_type"] == "平板"


def test_real_sdk_request_and_response_path_with_mocked_http(tmp_path):
    """Exercises the actual anthropic SDK call made in live mode, with the HTTP layer mocked."""
    anthropic = pytest.importorskip("anthropic")
    import json

    try:  # newer SDKs ship their own httpx fork
        import httpx2 as httpx
    except ImportError:
        import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant", "model": seen["body"]["model"],
            "content": [{"type": "tool_use", "id": "toolu_1", "name": "report", "input": {"verdict": "accept"}}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 123, "output_tokens": 45},
        })

    client = ClaudeClient(model="claude-sonnet-5", mode="live", cache_dir=tmp_path, api_key="sk-test")
    client._client = anthropic.Anthropic(api_key="sk-test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert client.complete_json("sys", "user", SCHEMA) == {"verdict": "accept"}
    assert seen["url"].endswith("/v1/messages")
    assert seen["key"] == "sk-test"
    assert seen["body"]["tool_choice"] == {"type": "tool", "name": "report"}
    assert client.usage.input_tokens == 123 and client.usage.output_tokens == 45
    assert not list(tmp_path.glob("*.json"))  # live mode does not cache
