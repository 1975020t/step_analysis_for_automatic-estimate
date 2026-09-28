"""Holdout-only presentation variants for the drawing-PDF golden set.

DO NOT READ THIS FILE WHILE DEVELOPING A READER. It exists to measure generalization: the development set
(unseen_ratio = 0) never uses it; the holdout (unseen_ratio = 0.2) draws about a fifth of its drawings with
a layout and label wording that the development set does not contain. The meaning of the conditions and
the truth rules are the same as in golden/pdf_golden.py - only the presentation differs.
"""
from __future__ import annotations

import random

from golden import pdf_render as R

UNSEEN_LABELS = {
    "ja": {"drawing_no": ["図面No.", "DWG番号"], "part_name": ["品目名", "部品名称"], "material": ["材料指定", "素材"],
           "thickness": ["厚み", "材厚"], "mat_thk": ["素材／厚み"], "finish": ["仕上げ", "後処理"],
           "quantity": ["手配数量", "必要数"], "revision": ["訂正", "Rev."], "due": ["希望納期"], "scale": ["縮尺"],
           "date": ["発行日"]},
    "en": {"drawing_no": ["PART NO.", "DOC NO."], "part_name": ["ITEM"], "material": ["MATL", "RAW MATERIAL"],
           "thickness": ["SHEET THK", "GAUGE / THK"], "mat_thk": ["MATL / GAUGE"], "finish": ["COATING", "PLATING / FINISH"],
           "quantity": ["ORDER QTY", "PCS REQ'D"], "revision": ["ISSUE"], "due": ["REQ'D DATE"], "scale": ["SCALE"],
           "date": ["ISSUED"]},
}


def choose_style(rng: random.Random) -> str:
    if rng.random() < 0.5:
        return "spec_sheet"
    return rng.choice(["jis_cad", "iso_en", "jis_bom", "bilingual"])


def labels(rng: random.Random, lang: str) -> dict:
    return UNSEEN_LABELS[lang]


def layout_spec_sheet(sh, plan, views, page_size):
    """A 'parts manufacturing specification' sheet (A4 portrait): a two-column spec table on top, remarks,
    views below, revision table at the bottom right. No title block."""
    from golden.pdf_golden import callout_column, draw_views, revision_table
    rng = plan.rng
    sh.text(rng.choice(["部品製作仕様書", "製作依頼書（部品）"]), 105, 282, 6.0, anchor="c")
    sh.text(f"{plan.company}　発行日 {plan.date}", 195, 274, 2.8, anchor="r")
    rows = [["項目", "内容"]]
    tags = {}
    for k, v, tag in plan.title:
        label = plan.labels_chosen.setdefault(k, rng.choice(plan.labels[k]))
        rows.append([label, v])
        if tag:
            tags[(len(rows) - 1, 1)] = tag
    bottom = sh.table(15, 270, [40, 140], rows, row_h=7.0, size=3.2, tags=tags)
    y = bottom - 7
    if plan.notes:
        sh.text("備考", 15, y, 3.4)
        y -= 5.5
        for k, (text, tag) in enumerate(plan.notes):
            sh.text(f"（{k + 1}）{text}", 18, y, 2.9, tag=tag, max_w=175)
            y -= 5.0
    rev_bottom = revision_table(sh, plan, 105, 62, 90, "ja") if plan.revisions else 62
    top_origin, s, views_right = draw_views(sh, views, (15, max(rev_bottom, 20) + 6, 118, y - max(rev_bottom, 20) - 14), third=True, font=R.JA)
    callout_column(sh, views, rng, plan.callouts, top_origin, s, min(140, views_right + 4), y - 12)
    plan.regions["seal"] = (160, 262, 190, 264)
