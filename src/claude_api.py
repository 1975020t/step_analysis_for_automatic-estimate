"""Thin Claude API client for analysis experiments.

Only an API key is needed: put ANTHROPIC_API_KEY in .env (git-ignored).

Environment variables (all optional except the key for live calls):
  ANTHROPIC_API_KEY   API key
  ANTHROPIC_MODEL     model id (default: claude-sonnet-5)
  CLAUDE_CACHE_MODE   record (default) | replay | live
                        record: reuse a cached response if present, otherwise call the API and cache it
                        replay: cached responses only; a cache miss raises (no network, no cost) - used by tests
                        live:   always call the API, never read or write the cache
  CLAUDE_CACHE_DIR    cache directory (default: .claude_cache, git-ignored)

The cache key is a hash of (model, system, messages, tools), so re-running the harness on the same
parts costs nothing, and recorded responses can be committed as test fixtures.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "claude-sonnet-5"
ROOT = Path(__file__).resolve().parents[1]


class ClaudeConfigError(RuntimeError):
    pass


class ClaudeCacheMiss(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    models: set = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {"llm_calls": self.calls, "llm_cache_hits": self.cache_hits, "llm_input_tokens": self.input_tokens,
                "llm_output_tokens": self.output_tokens, "llm_seconds": round(self.seconds, 2),
                "llm_model": ",".join(sorted(self.models))}


def _load_dotenv():
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


class ClaudeClient:
    def __init__(self, model: str | None = None, mode: str | None = None, cache_dir: str | Path | None = None,
                 api_key: str | None = None, max_retries: int = 3):
        _load_dotenv()
        self.model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self.mode = (mode or os.getenv("CLAUDE_CACHE_MODE", "record")).lower()
        if self.mode not in {"record", "replay", "live"}:
            raise ClaudeConfigError("CLAUDE_CACHE_MODE は record / replay / live のいずれかです")
        self.cache_dir = Path(cache_dir or os.getenv("CLAUDE_CACHE_DIR", ROOT / ".claude_cache"))
        self._api_key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.max_retries = max_retries
        self.usage = Usage()
        self._client = None

    # ------------------------------------------------------------ public
    def complete_json(self, system: str, user: str, schema: dict, tool_name: str = "report",
                      max_tokens: int = 2048) -> dict:
        """Ask Claude for a JSON object matching `schema` (forced tool call). Returns the object."""
        request = {
            "model": self.model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": tool_name, "description": "Return the result as structured data.",
                       "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        response = self._call(request)
        for block in response["content"]:
            if block.get("type") == "tool_use":
                return block["input"]
        raise RuntimeError("Claude API の応答に構造化結果が含まれていません")

    # ------------------------------------------------------------ internals
    def _key(self, request: dict) -> str:
        payload = json.dumps(request, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def _call(self, request: dict) -> dict:
        path = self.cache_dir / f"{self._key(request)}.json"
        self.usage.models.add(request["model"])
        if self.mode in {"record", "replay"} and path.exists():
            self.usage.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        if self.mode == "replay":
            raise ClaudeCacheMiss(f"記録済みの応答がありません（replayモード）: {path.name}")
        response = self._live(request)
        if self.mode == "record":
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"request": request, "response": response}, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        return response

    def _live(self, request: dict) -> dict:
        if not self._api_key:
            raise ClaudeConfigError("ANTHROPIC_API_KEY が未設定です。.env に記入してください")
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key, max_retries=self.max_retries)
        started = time.time()
        message = self._client.messages.create(**request)
        self.usage.seconds += time.time() - started
        self.usage.calls += 1
        self.usage.input_tokens += message.usage.input_tokens
        self.usage.output_tokens += message.usage.output_tokens
        return message.model_dump(mode="json")
