from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from src.models import QuoteCondition, QuoteResult, SheetMetalAnalysis
from src.quote_engine import QuoteEngine
from src.sheetmetal_analyzer import SheetMetalAnalyzer


class QuoteService:
    def __init__(self, analyzer: SheetMetalAnalyzer, engine: QuoteEngine) -> None:
        self.analyzer = analyzer
        self.engine = engine

    def create_quote(
        self,
        source: str | Path | BinaryIO,
        condition: QuoteCondition,
        file_name: str | None = None,
    ) -> tuple[SheetMetalAnalysis, QuoteResult]:
        analysis = self.analyzer.analyze(source, file_name=file_name)
        return analysis, self.engine.calculate(analysis, condition)
