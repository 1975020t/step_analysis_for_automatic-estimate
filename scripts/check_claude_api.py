"""Check that the Claude API key in .env works (one tiny request, ~100 tokens).

    python scripts/check_claude_api.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.claude_api import ClaudeClient, ClaudeConfigError  # noqa: E402


def main() -> int:
    client = ClaudeClient(mode="live")
    print(f"model: {client.model}")
    try:
        answer = client.complete_json(
            "You are a connectivity check.", "Reply with ok=true.",
            {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}, max_tokens=64)
    except ClaudeConfigError as exc:
        print(f"設定エラー: {exc}")
        return 2
    except Exception as exc:  # show the API error as-is (auth, model name, network ...)
        print(f"API呼び出しに失敗しました: {type(exc).__name__}: {exc}")
        return 1
    usage = client.usage.as_dict()
    print(f"応答: {answer}  入力 {usage['llm_input_tokens']} / 出力 {usage['llm_output_tokens']} トークン")
    print("OK: Claude API を利用できます")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
