from __future__ import annotations

from copy import deepcopy

from src.llm_client import BaseLLMClient
from src.master_loader import MasterLoader
from src.models import AdditionalProcess, ChatApplyResult, QuoteCondition


class ChatQuoteService:
    def __init__(self, llm: BaseLLMClient, masters: MasterLoader) -> None:
        self.llm = llm
        self.masters = masters

    def interpret_and_apply(
        self,
        user_text: str,
        condition: QuoteCondition,
        chat_history: list[dict] | None = None,
        pending_confirmation: str | None = None,
    ) -> ChatApplyResult:
        interpretation = self.llm.interpret_quote_change(
            user_text=user_text,
            condition=condition,
            process_catalog=self.masters.llm_process_catalog(),
            chat_history=chat_history,
            pending_confirmation=pending_confirmation,
        )
        updated = deepcopy(condition)

        # needs_confirmation時でも、一意に解釈できた操作（例: 皿もみ）は適用する。
        for operation in interpretation.operations:
            if operation.field == "material":
                material = str(operation.value)
                self.masters.material(material)
                updated.material = material
                continue
            if operation.field == "quantity":
                raw_quantity = (
                    operation.value if operation.value is not None else operation.quantity
                )
                quantity = int(raw_quantity or 0)
                if quantity <= 0:
                    raise ValueError("数量は1以上で指定してください")
                updated.quantity = quantity
                continue

            code = operation.process_code or ""
            # 曖昧表現の確認待ちでは、Structured Outputsが空の工程操作を
            # 返す場合がある。価格へ渡さず安全に無視する。
            if not code:
                continue
            if code == "UNKNOWN_PROCESS":
                continue
            self.masters.process(code)
            existing = next(
                (item for item in updated.additional_processes if item.process_code == code), None
            )
            if operation.action == "remove":
                updated.additional_processes = [
                    item for item in updated.additional_processes if item.process_code != code
                ]
            elif existing:
                existing.quantity = operation.quantity or existing.quantity
                existing.unit = operation.unit or existing.unit
                existing.user_text = user_text
            else:
                updated.additional_processes.append(
                    AdditionalProcess(
                        process_code=code,
                        quantity=operation.quantity or 1,
                        unit=operation.unit or self.masters.process(code)["unit"],
                        user_text=user_text,
                    )
                )

        if interpretation.status == "ready":
            message = "条件を更新し、CSV単価で見積を再計算しました。"
        else:
            message = interpretation.confirmation_message or "条件を確認してください。"
        return ChatApplyResult(
            condition=updated,
            interpretation=interpretation,
            message=message,
        )
