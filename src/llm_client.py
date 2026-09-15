from __future__ import annotations

import json
import os
import re
from typing import Protocol

from src.models import ChatInterpretation, QuoteCondition, QuoteOperation


class BaseLLMClient(Protocol):
    def interpret_quote_change(
        self,
        user_text: str,
        condition: QuoteCondition,
        process_catalog: list[dict],
        chat_history: list[dict] | None = None,
        pending_confirmation: str | None = None,
    ) -> ChatInterpretation: ...


def _extract_quantity(text: str, default: float = 1) -> float:
    patterns = [r"(\d+(?:\.\d+)?)\s*(?:箇所|個|穴|つ)", r"数量\s*(\d+(?:\.\d+)?)"]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    return default


class MockLLMClient:
    """APIキーなしで受入シナリオを再現する決定論的な解釈器。"""

    mode = "mock"

    def interpret_quote_change(
        self,
        user_text: str,
        condition: QuoteCondition,
        process_catalog: list[dict],
        chat_history: list[dict] | None = None,
        pending_confirmation: str | None = None,
    ) -> ChatInterpretation:
        text = user_text.strip()
        operations: list[QuoteOperation] = []

        if pending_confirmation and "AL5052" in pending_confirmation and text.lower() in {
            "はい", "yes", "ok", "お願いします", "それで"
        }:
            return ChatInterpretation(
                status="ready",
                operations=[QuoteOperation(action="update", field="material", value="AL5052")],
            )

        material_codes = [code for code in ("AL5052", "SUS304", "SS400") if code.lower() in text.lower()]
        if material_codes:
            operations.append(
                QuoteOperation(action="update", field="material", value=material_codes[-1])
            )
        elif "アルミ" in text:
            return ChatInterpretation(
                status="needs_confirmation",
                operations=operations,
                confirmation_message="AL5052の意味で合っていますか？",
            )
        elif "SUS" in text.upper() or "ステンレス" in text:
            operations.append(QuoteOperation(action="update", field="material", value="SUS304"))

        quantity_match = re.search(r"(?:数量\s*)?(\d+)\s*個", text)
        if quantity_match and not any(word in text for word in ("皿", "穴", "バフ", "研磨")):
            operations.append(
                QuoteOperation(
                    action="update", field="quantity", value=int(quantity_match.group(1))
                )
            )

        wants_remove = any(word in text for word in ("なし", "削除", "やめ", "不要"))
        if "皿もみ" in text or "皿穴" in text:
            operations.append(
                QuoteOperation(
                    action="remove" if wants_remove else "add",
                    process_code="COUNTERSINK",
                    quantity=_extract_quantity(text),
                    unit="hole",
                )
            )
        if "鏡面" in text:
            operations.append(
                QuoteOperation(
                    action="remove" if wants_remove else "add",
                    process_code="BUFF_MIRROR",
                    quantity=1,
                    unit="job",
                )
            )
        if "400番" in text or "#400" in text or "バフ400" in text:
            if "鏡面じゃなく" in text or "鏡面ではなく" in text:
                operations.append(QuoteOperation(action="remove", process_code="BUFF_MIRROR"))
            operations.append(
                QuoteOperation(
                    action="remove" if wants_remove else "add",
                    process_code="BUFF_400",
                    quantity=1,
                    unit="job",
                )
            )
        elif "バフ" in text and "鏡面" not in text:
            return ChatInterpretation(
                status="needs_confirmation",
                operations=operations,
                confirmation_message=(
                    "「バフ仕上げ」は「#400バフ仕上げ」と「鏡面バフ仕上げ」の"
                    "どちらを意味しますか？"
                ),
            )

        if "研磨" in text:
            operations.append(
                QuoteOperation(
                    action="remove" if wants_remove else "add",
                    process_code="POLISH",
                    quantity=1,
                    unit="job",
                )
            )

        if operations:
            return ChatInterpretation(status="ready", operations=operations)
        return ChatInterpretation(
            status="unknown",
            operations=[],
            confirmation_message=(
                "該当する登録工程または変更条件が見つかりません。"
                "見積担当者による確認・単価登録が必要です。"
            ),
        )


class OpenAILLMClient:
    mode = "openai"
    def __init__(self, api_key: str, model: str = "gpt-4.1-mini") -> None:
        if not api_key or api_key.startswith("YOUR_"):
            raise ValueError("有効なOPENAI_API_KEYを設定してください")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openaiパッケージがインストールされていません") from exc
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def interpret_quote_change(
        self,
        user_text: str,
        condition: QuoteCondition,
        process_catalog: list[dict],
        chat_history: list[dict] | None = None,
        pending_confirmation: str | None = None,
    ) -> ChatInterpretation:
        system_prompt = """あなたは製造見積の条件解釈器です。
ユーザー発言を構造化操作へ変換してください。金額は絶対に生成・推測しません。
工程はprocess_catalogに存在するprocess_codeだけを使います。
工程が複数候補ならneeds_confirmation、存在しなければunknownにします。
材料はAL5052/SUS304/SS400、数量は正の整数だけを許可します。
工程以外の変更はfieldをmaterialまたはquantityにしてください。

重要な変換例:
- 「数量50個ならいくら？」→ status=ready, action=update, field=quantity,
  value=50（またはquantity=50）。「いくら」は無視し、価格を回答しない。
- 「10個じゃなくて50個」→ status=ready, action=update, field=quantity, value=50。
- 「材料をSUS304に変更」→ status=ready, action=update, field=material,
  value="SUS304"。
- 「バフ」だけで種類が不明→ status=needs_confirmation。空の工程操作は返してもよい。
- マスタにない工程→ status=unknown。価格や工程を新規作成しない。"""
        context = {
            "current_condition": {
                "material_code": condition.material,
                "quantity": condition.quantity,
                "additional_processes": [
                    {
                        "process_code": item.process_code,
                        "quantity": item.quantity,
                        "unit": item.unit,
                    }
                    for item in condition.additional_processes
                ],
            },
            "material_codes": ["AL5052", "SUS304", "SS400"],
            "process_codes": [item["process_code"] for item in process_catalog],
            "pending_confirmation": pending_confirmation,
            "recent_chat_text": [
                {"role": item.get("role", "user"), "content": str(item.get("content", ""))}
                for item in (chat_history or [])[-8:]
            ],
            "user_text": user_text,
        }
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            text_format=ChatInterpretation,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise RuntimeError("OpenAI APIから構造化結果を取得できませんでした")
        return parsed


def build_llm_client() -> BaseLLMClient:
    mode = os.getenv("LLM_MODE", "auto").strip().lower()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    has_real_key = bool(api_key and not api_key.startswith("YOUR_"))
    if mode == "openai" and not has_real_key:
        raise RuntimeError("LLM_MODE=openaiですが、有効なOPENAI_API_KEYがありません")
    if mode in {"auto", "openai"} and has_real_key:
        return OpenAILLMClient(api_key, os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    if mode not in {"auto", "mock", "openai"}:
        raise ValueError("LLM_MODEはauto、mock、openaiのいずれかで指定してください")
    return MockLLMClient()
