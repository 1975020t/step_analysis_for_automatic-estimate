"""Claude API scaffolding tests. None of these call the network."""
from __future__ import annotations

import pytest

from src.claude_api import ClaudeCacheMiss, ClaudeClient, ClaudeConfigError
from src.llm_only_analyzer import LLMOnlyAnalyzer

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


ANSWER = {"reasoning": "test", "part_type": "平板", "thickness_mm": 2.0, "blank_area_mm2": 4921.5,
          "cut_length_mm": 331.4, "hole_count": 1, "bends": []}


def test_llm_only_analyzer_returns_llm_values_always_as_estimate(tmp_path):
    cq = pytest.importorskip("cadquery")
    shape = cq.Workplane("XY").box(100, 50, 2.0).faces(">Z").workplane().hole(10).val()
    path = tmp_path / "plate.step"
    cq.exporters.export(shape, str(path))
    seen = {}

    def fake(request):
        seen["user"] = request["messages"][0]["content"]
        return _tool_response(ANSWER)

    client = ClaudeClient(model="test-model", mode="record", cache_dir=tmp_path / "cache", api_key="dummy")
    client._live = fake
    result = LLMOnlyAnalyzer(k_factor=0.5, k_factor_is_default=False, client=client).analyze(path)

    assert result.status == "partial"  # never a confirmed quote
    assert result.reason_codes == ["LLM_ONLY_UNVERIFIED"]
    assert (result.thickness_mm, result.blank_area_mm2, result.hole_count, result.bend_count) == (2.0, 4921.5, 1, 0)
    assert all(q.confidence == "low" for q in result.metric_quality.values())
    assert '"type": "CYLINDER"' in seen["user"] and "volume_mm3" in seen["user"]
    assert "Kファクター: 0.5" in seen["user"]


def test_llm_only_reports_hems_from_bend_angles():
    answer = {**ANSWER, "bends": [{"angle_deg": 180, "inner_radius_mm": 1.0}, {"angle_deg": 90, "inner_radius_mm": 2.0}]}
    result = LLMOnlyAnalyzer(k_factor=0.5, k_factor_is_default=False, client=object()).to_analysis("x.step", answer)
    assert result.bend_count == 2
    assert [b.angle_deg for b in result.bend_evidence] == [180, 90]


def test_llm_only_rejects_invalid_output_and_api_errors(tmp_path):
    analyzer = LLMOnlyAnalyzer(k_factor=0.5, k_factor_is_default=False, client=object())
    assert analyzer.to_analysis("x.step", {**ANSWER, "blank_area_mm2": -1}).status == "unsupported"
    assert analyzer.to_analysis("x.step", {"reasoning": "?"}).reason_code == "LLM_OUTPUT_INVALID"

    cq = pytest.importorskip("cadquery")
    path = tmp_path / "plate.step"
    cq.exporters.export(cq.Workplane("XY").box(40, 30, 2.0).val(), str(path))
    client = ClaudeClient(model="test-model", mode="replay", cache_dir=tmp_path / "empty", api_key="")
    result = LLMOnlyAnalyzer(k_factor=0.5, k_factor_is_default=False, client=client).analyze(path)
    assert (result.status, result.reason_code, result.blank_area_mm2) == ("error", "LLM_API_ERROR", None)


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
