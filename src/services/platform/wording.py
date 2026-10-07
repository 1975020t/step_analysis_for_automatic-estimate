"""What the screens say about a CAD analysis, in the estimator's words.

The analyzers (src/sheetmetal_analyzer.py, src/dxf_analyzer.py) write messages for developers and the evaluation
("自己検算が一致しました", "$INSUNITS", "デモ既定値"). They are not changed (their results must stay as they are);
the screens show these translations instead. Unknown texts pass through unchanged.
"""
from __future__ import annotations

import re

FAILURE = {
    "INVALID_FILE_TYPE": "ファイルの種類が違います",
    "STEP_READ_ERROR": "ファイルを読み込めませんでした",
    "DXF_READ_ERROR": "ファイルを読み込めませんでした",
    "ANALYSIS_ERROR": "寸法を求められませんでした",
    "ANALYZER_UNAVAILABLE": "寸法を求められませんでした",
    "NO_SOLID": "部品の形が見つかりませんでした",
    "MULTIPLE_SOLIDS": "1つのファイルに複数の部品が入っています",
    "MULTIPLE_PARTS": "1つのファイルに複数の部品が入っています",
    "BREP_INVALID": "形のデータに不具合があります",
    "NON_CONSTANT_THICKNESS": "板厚が一定の板金部品として読めませんでした",
    "NOT_SHEET_METAL": "板金部品として読めませんでした",
    "BEND_UNDETERMINED": "曲げの位置を判定できませんでした",
    "FLAT_PATTERN_FAILED": "展開図を作れませんでした",
    "NO_CUT_CONTOUR": "外形の線が見つかりませんでした",
    "OPEN_CONTOUR": "外形の線が閉じていません",
    "UNEXPLAINED_GEOMETRY": "外形を1つにまとめられませんでした",
    "THICKNESS_REQUIRED": "板厚が入力されていません",
}

# (pattern, replacement or None to drop). Applied to assumptions and warnings.
RULES: list[tuple[str, str | None]] = [
    (r"^Kファクターはデモ既定値([\d.]+)を使用しました。?$", r"曲げの伸び（Kファクター）が未指定のため、標準値\1で展開しました"),
    (r"^Kファクターが未指定のため.*$", None),
    (r"^DXFに単位（\$INSUNITS）がないため、mmとして扱いました。?$", "図面の単位が書かれていないため、mm として読みました"),
    (r"^DXFの単位が不明です。.*$", None),
    (r"^DXFの単位（\$INSUNITS）が(.+?)のため、1単位＝([\d.]+) mm で換算しました。?$", r"図面の単位が\1のため、mm に換算しました（1単位＝\2 mm）"),
    (r"^DXFの単位が(.+?)に設定されています。(.*)$", None),
    (r"^展開図の自己検算が一致しません.*$", "展開寸法を確かめきれませんでした"),
    (r"^2D展開図を構築できず.*$", "展開図を作れなかったため、展開面積と切断長は推定値です"),
    (r"^曲げ部にかかる穴・切欠きがあります.*$", "曲げの部分に穴・切欠きがあります"),
    (r"^見積用の展開です.*$", None),
    (r"^輪郭のすき間（最大 ([\d.]+) mm）を閉じて解析しました.*$", r"外形の線のすき間（最大 \1 mm）をつないで読みました。図面を確認してください"),
    (r"^部品内の別色の実線（ケガキ線など）は切断線から除外しました。?$", "ケガキ線など色の違う線は切断線に含めていません"),
    (r"^閉じたすき間の最大値.*$", None),
    (r"^LLM.*$", None),
]


def friendly(text: str) -> str | None:
    t = (text or "").strip()
    for pattern, replacement in RULES:
        if re.match(pattern, t):
            return None if replacement is None else re.sub(pattern, replacement, t).rstrip("。")
    return t.rstrip("。") or None


def notes(analysis: dict | None) -> list[str]:
    """Why the values are estimates, without duplicates, in the estimator's words."""
    out: list[str] = []
    a = analysis or {}
    for text in list(a.get("assumptions") or []) + list(a.get("warnings") or []):
        line = friendly(text)
        if line and line not in out:
            out.append(line)
    return out


def failure(analysis: dict | None) -> str:
    """Why the CAD data gave no dimensions (解析不可)."""
    code = (analysis or {}).get("reason_code") or ""
    return FAILURE.get(code, "CADデータから寸法を求められませんでした")


# Why a drawing reading needs a look (src/pdf_reader.py review_reasons), in the estimator's words.
REVIEW = [
    (r"^規則で解釈できない加工指示", "解釈できない加工指示があります"),
    (r"^規則で解釈できない表記", "書き方を解釈できません"),
    (r"^規則とLLMの.*異なる", "読み方が2通りに分かれました"),
    (r"^規則で数値を読み取れない", "数値を読み取れません"),
    (r"^最新の改訂の値と異なる", "改訂欄の値と本文の値が違います"),
    (r"^テキスト層に見つからない", "図面の文字と一致しません"),
    (r"^テキスト層にある加工指示が読み取り結果にない:\s*(.*)$", r"図面の加工指示を読み落としている可能性があります（\1）"),
    (r"^部品表の合計が部品数量で割り切れない", "部品表の数量が割り切れません"),
    (r"^加工の個数を読み取れない", "加工の個数を読み取れません"),
    (r"^特急の根拠となる語がない", "特急かどうかがはっきりしません"),
    (r"^手書き・押印と印刷の特急の指示が異なる", "手書き・押印と印刷で特急の指示が違います"),
    (r"^2回の読み取りが一致しない", "読み取るたびに結果が変わりました"),
]


def review_reason(text: str) -> str:
    t = (text or "").strip()
    for pattern, replacement in REVIEW:
        if re.match(pattern, t):
            return re.sub(pattern + (r".*$" if r"\1" not in replacement else ""), replacement, t, count=1)
    return t
