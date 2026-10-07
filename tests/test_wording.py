"""What the screens say about an analysis and a reading: the estimator's words, not the analyzers' messages."""
from __future__ import annotations

from src.services.platform.pricing import count_in, without_count
from src.services.platform.wording import failure, friendly, notes, review_reason


def test_analyzer_messages_become_the_estimators_words():
    analysis = {"assumptions": ["Kファクターはデモ既定値0.33を使用しました。", "Kファクターが未指定のため、曲げ部品は概算です。"],
                "warnings": ["DXFの単位（$INSUNITS）がinchのため、1単位＝25.4 mm で換算しました。", "LLMの補助は使っていません"]}
    assert notes(analysis) == ["曲げの伸び（Kファクター）が未指定のため、標準値0.33で展開しました",
                               "図面の単位がinchのため、mm に換算しました（1単位＝25.4 mm）"]
    assert friendly("DXFに単位（$INSUNITS）がないため、mmとして扱いました。") == "図面の単位が書かれていないため、mm として読みました"
    assert friendly("展開図の自己検算が一致しません（差 0.3%）") == "展開寸法を確かめきれませんでした"
    for text in notes(analysis):
        assert not any(w in text for w in ("デモ", "$INSUNITS", "自己検算", "LLM"))
    assert failure({"reason_code": "NOT_SHEET_METAL"}) == "板金部品として読めませんでした"
    assert failure({"reason_code": "SOMETHING_NEW"}) == "CADデータから寸法を求められませんでした"


def test_reading_review_reasons_become_the_estimators_words():
    assert review_reason("規則とLLMの読み取り結果が異なる") == "読み方が2通りに分かれました"
    assert review_reason("テキスト層にある加工指示が読み取り結果にない: M10タップ 6ヶ所") == \
        "図面の加工指示を読み落としている可能性があります（M10タップ 6ヶ所）"
    assert review_reason("数量：図面に記載なし") == "数量：図面に記載なし"  # unknown texts pass through


def test_the_count_moves_from_the_name_to_the_quantity():
    for text, name, count in (("M10タップ 6ヶ所", "M10タップ", 6), ("バーリングタップ M4 2ヶ所", "バーリングタップ M4", 2),
                              ("4X M10 TAP", "M10 TAP", 4), ("PEM ナット ×3", "PEM ナット", 3), ("溶接", "溶接", None)):
        assert without_count(text) == name and count_in(text) == count
