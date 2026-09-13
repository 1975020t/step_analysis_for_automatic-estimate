from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from src.cad_analyzer import CadAnalyzer
from src.models import CadFeatures, QuoteCondition, QuoteResult
from src.quote_engine import QuoteEngine


class QuoteService:
    def __init__(self, analyzer: CadAnalyzer, engine: QuoteEngine) -> None:
        self.analyzer = analyzer
        self.engine = engine

    def create_quote(
        self,
        source: str | Path | BinaryIO,
        condition: QuoteCondition,
        file_name: str | None = None,
    ) -> tuple[CadFeatures, QuoteResult]:
        features = self.analyzer.analyze(source, file_name=file_name)
        return features, self.engine.calculate(features, condition)

