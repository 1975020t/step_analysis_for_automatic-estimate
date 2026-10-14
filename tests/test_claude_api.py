"""Claude API scaffolding tests. None of these call the network."""
from __future__ import annotations

import pytest

from src.claude_api import ClaudeCacheMiss, ClaudeClient, ClaudeConfigError

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
    monkeypatch.setattr("src.claude_api._load_dotenv", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANALYSIS_ANTHROPIC_API_KEY", raising=False)
    client = ClaudeClient(model="test-model", mode="live", cache_dir=tmp_path, api_key="")
    with pytest.raises(ClaudeConfigError):
        client.complete_json("sys", "user", SCHEMA)


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


def test_project_specific_key_name_is_preferred(tmp_path, monkeypatch):
    monkeypatch.setattr("src.claude_api._load_dotenv", lambda: None)  # ignore any local .env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "generic")
    monkeypatch.setenv("ANALYSIS_ANTHROPIC_API_KEY", "project")
    assert ClaudeClient(mode="live", cache_dir=tmp_path)._api_key == "project"
    monkeypatch.delenv("ANALYSIS_ANTHROPIC_API_KEY")
    assert ClaudeClient(mode="live", cache_dir=tmp_path)._api_key == "generic"
