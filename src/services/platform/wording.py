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
    "PDF_SHAPE_ESTIMATE": "図面PDFから寸法を求められませんでした",
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
    # the shape estimated from a drawing PDF alone (src/pdf_shape.py)
    (r"^図面PDFから推定した値.*$", "形状の値は図面PDFからの概算です（図面の外形寸法・曲げ数・穴数から計算）"),
    (r"^(曲げ数|穴数)が画像の計測（(\d+)）とLLMの読み取り（(.+?)）で違います.*$", r"\1は図面の展開図の線から数えた \2 を使っています（図面全体を見た読み取りでは \3）"),
    (r"^(曲げ数|穴数)がLLMの2回の読み取りで違います（(.+?) と (.+?)）.*$", r"\1は読み取るたびに結果が変わりました（\2 と \3）。図を拡大して数えた \3 を使っています"),
    (r"^曲げ数・穴数を画像で確かめられませんでした（(.+)）$", r"曲げ数・穴数は図面を読み取った値で、線の数え上げでは確かめていません（\1）"),
    (r"^図面の文字にない外形寸法を読み取りました: (.*)$", r"外形寸法（\1）が図面の文字と一致しません。図面で確認してください"),
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


# Input errors (pydantic validation) in the estimator's words instead of the library's English.
FIELD_NAMES = {
    "customer": "顧客", "title": "案件名", "staff": "担当", "quantity": "数量", "due_date": "希望納期", "material": "材質",
    "custom_material": "マスタにない材質", "surface_treatment": "表面処理", "custom_finish": "マスタにない表面処理",
    "rush": "特急", "processes": "追加加工", "custom_processes": "マスタにない加工", "k_factor": "Kファクター",
    "thickness_mm": "板厚", "flat_confirmed": "曲げなし（平板）", "shape": "寸法の値", "name": "名前",
    "price_per_kg": "kg単価", "density_kg_m3": "密度", "unit_price": "単価", "blank_area_mm2": "展開面積",
    "cut_length_mm": "切断長", "hole_count": "穴数", "bend_count": "曲げ数", "drawing_no": "図番", "label": "項目名",
    "competitor_price": "他社価格", "statuses": "ステータス", "staff_list": "担当者", "text": "メモ", "version": "版",
    "status_id": "ステータス", "drawing_id": "図面", "code": "コード", "x": "位置", "y": "位置",
}
_KIND = {"string_too_short": "を入力してください", "missing": "を入力してください", "greater_than": "は0より大きい値にしてください",
         "greater_than_equal": "は0以上にしてください", "less_than_equal": "が大きすぎます", "less_than": "が大きすぎます",
         "int_parsing": "は整数で入力してください", "int_from_float": "は整数で入力してください",
         "float_parsing": "は数値で入力してください", "date_from_datetime_parsing": "の日付が正しくありません",
         "date_parsing": "の日付が正しくありません", "bool_parsing": "が正しくありません"}


def input_error(error: dict) -> str:
    """One pydantic error as 「数量は0より大きい値にしてください」."""
    names = [FIELD_NAMES[str(x)] for x in error.get("loc", []) if str(x) in FIELD_NAMES]
    field = names[-1] if names else "入力"
    if len(names) > 1 and names[0] != field:
        field = f"{names[0]}の{field}"
    return f"{field}{_KIND.get(error.get('type', ''), 'が正しくありません')}"


def input_errors(errors: list[dict]) -> str:
    return "／".join(dict.fromkeys(input_error(e) for e in errors))
