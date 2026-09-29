"""Charts of the evaluation report (docs/images/charts/*.svg). The numbers are read from the tables of the
reports in analysis/ (nothing is typed in by hand), so the charts always match the reports.

    python docs/scripts/make_charts.py
"""
from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / "analysis"
OUT = ROOT / "docs" / "images" / "charts"
FONT = ROOT / "fonts" / "ipag.ttf"
font_manager.fontManager.addfont(str(FONT))
plt.rcParams.update({"font.family": font_manager.FontProperties(fname=str(FONT)).get_name(), "font.size": 10,
                     "svg.fonttype": "path", "axes.spines.top": False, "axes.spines.right": False})
BLUE, LIGHT, ORANGE, RED, GREEN, GREY = "#1f6aa5", "#9cc3e4", "#e39b25", "#c8413b", "#3c8c3c", "#9aa8b6"


def tables(name: str) -> list[list[dict[str, str]]]:
    """All markdown tables of analysis/<name>.md as lists of row dicts."""
    lines = (A / f"{name}.md").read_text(encoding="utf-8").splitlines()
    found, i = [], 0
    while i < len(lines):
        if lines[i].startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1]):
            header = [c.strip() for c in lines[i].strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip("|").split("|")]
                rows.append(dict(zip(header, cells)))
                i += 1
            found.append(rows)
        else:
            i += 1
    return found


def table_with(name: str, column: str) -> list[dict[str, str]]:
    return next(t for t in tables(name) if t and column in t[0])


def pct(text: str) -> float:
    return float(re.sub(r"[^\d.\-]", "", text.replace("**", "")) or 0)


def save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT / f"{name}.svg")
    plt.close(fig)
    print("saved", name)


def bars(ax, labels, series, colors, ylabel="%", ylim=(0, 105), fmt="{:.0f}", width=0.8):
    n = len(series)
    w = width / n
    for k, (label, values) in enumerate(series.items()):
        xs = [i + (k - (n - 1) / 2) * w for i in range(len(labels))]
        rects = ax.bar(xs, values, w * 0.92, label=label, color=colors[k])
        for r, v in zip(rects, values):
            ax.text(r.get_x() + r.get_width() / 2, r.get_height() + ylim[1] * 0.01, fmt.format(v), ha="center",
                    va="bottom", fontsize=7.5)
    ax.set_xticks(range(len(labels)), labels)
    ax.set_ylim(*ylim)
    ax.set_ylabel(ylabel)
    ax.legend(frameon=False, fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=min(n, 4))


# ---------------------------------------------------------------- STEP
def step_before_after():
    before = table_with("golden_eval_baseline_10pct", "レベル")
    after = table_with("golden_eval_latest", "レベル")
    labels = [r["レベル"] for r in after]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
    bars(axes[0], labels, {"改善前": [pct(r["**解析成功**"]) for r in before], "現在": [pct(r["**解析成功**"]) for r in after]},
         [GREY, BLUE])
    axes[0].set_title("解析成功（値が±10%以内で数も一致）")
    bars(axes[1], labels, {"改善前": [pct(r["**危険誤答**"]) for r in before], "現在": [pct(r["**危険誤答**"]) for r in after]},
         [GREY, RED])
    axes[1].set_title("危険誤答（誤っているのに確定表示）")
    save(fig, "step_before_after")


def step_datasets():
    sets = [("開発用 seed=1\n(508件)", "golden_eval_latest"), ("ホールドアウト seed=2\n(200件)", "golden_eval_holdout"),
            ("追加検証 seed=3\n(1,000件)", "golden_eval_seed3"), ("追加検証 seed=4\n(1,000件)", "golden_eval_seed4")]
    fig, ax = plt.subplots(figsize=(9, 3.2))
    levels = [r["レベル"] for r in table_with("golden_eval_latest", "レベル")]
    series = {}
    for label, name in sets:
        rows = table_with(name, "レベル")
        series[label.replace("\n", " ")] = [pct(r["確定で正解"]) for r in rows]
    bars(ax, levels, series, [BLUE, "#2f8fcf", GREEN, "#6aa84f"], ylim=(0, 112))
    ax.set_title("STEPの解析：データごとの「確定で正解」の割合（レベル別）")
    save(fig, "step_datasets")


def step_stress():
    rows = table_with("stress_eval", "レベル")
    labels = [r["レベル"].replace("_", "\n") for r in rows]
    ok = [pct(r["確定で正解"]) for r in rows]
    est = [pct(r["正解だが概算"]) + pct(r["誤り（概算表示あり）"]) for r in rows]
    unsup = [pct(r["解析不可"]) for r in rows]
    danger = [pct(r["**危険誤答**"]) for r in rows]
    fig, ax = plt.subplots(figsize=(9.5, 3.4))
    ax.bar(labels, ok, color=BLUE, label="確定で正解")
    ax.bar(labels, est, bottom=ok, color=ORANGE, label="概算（担当者が確認）")
    ax.bar(labels, unsup, bottom=[a + b for a, b in zip(ok, est)], color=GREY, label="解析不可")
    ax.bar(labels, danger, bottom=[a + b + c for a, b, c in zip(ok, est, unsup)], color=RED, label="危険誤答")
    ax.set_ylim(0, 105)
    ax.set_ylabel("%")
    ax.tick_params(axis="x", labelsize=7.5)
    ax.set_title("ストレステスト（作り方を変えた形状、各40件）")
    ax.legend(frameon=False, fontsize=8.5, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=4)
    save(fig, "step_stress")


def step_llm():
    rows = table_with("llm_comparison", "構成")
    pick = [r for r in rows if not r["構成"].startswith("旧")]
    labels = ["ルールのみ\n（採用）", "＋LLMレビュー", "＋LLM独立カウント\n(A)", "＋LLM独立カウント\n(B)"]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.3))
    axes[0].bar(labels, [pct(r["確定で正解"]) for r in pick], color=BLUE, label="確定で正解")
    axes[0].bar(labels, [pct(r["正解だが概算"]) for r in pick], bottom=[pct(r["確定で正解"]) for r in pick], color=ORANGE,
                label="正解だが概算（誤警報）")
    axes[0].set_ylabel("件（100件中）")
    axes[0].set_title("結果の内訳")
    axes[0].tick_params(axis="x", labelsize=7.5)
    axes[0].legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.25), ncol=2)
    times = [pct(r["平均処理時間"]) for r in pick]
    axes[1].bar(labels, times, color=[BLUE, LIGHT, LIGHT, LIGHT])
    for i, t in enumerate(times):
        axes[1].text(i, t + 0.2, f"{t:g}秒", ha="center", fontsize=8)
    axes[1].set_ylabel("秒 / 部品")
    axes[1].set_title("平均処理時間")
    axes[1].tick_params(axis="x", labelsize=7.5)
    save(fig, "step_llm")


# ---------------------------------------------------------------- DXF
def dxf_compare():
    new = table_with("dxf_eval_latest", "変種")
    base = table_with("dxf_eval_baseline", "変種")
    labels = [r["変種"] for r in new]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
    bars(axes[0], labels, {"素朴な方法": [pct(r["**解析成功**"]) for r in base], "DxfAnalyzer": [pct(r["**解析成功**"]) for r in new]},
         [GREY, BLUE])
    axes[0].set_title("解析成功（Xは「確定しない」が正解）")
    bars(axes[1], labels, {"素朴な方法": [pct(r["**危険誤答**"]) for r in base], "DxfAnalyzer": [pct(r["**危険誤答**"]) for r in new]},
         [GREY, RED])
    axes[1].set_title("危険誤答")
    for ax in axes:
        ax.tick_params(axis="x", labelsize=7.5)
    save(fig, "dxf_compare")


# ---------------------------------------------------------------- drawing PDF
FIELDS = ["材質", "板厚", "数量", "表面処理", "追加加工", "特急", "図番", "改訂", "特記事項"]


def pdf_fields():
    new = table_with("pdf_eval_latest", "正解率")
    base = table_with("pdf_eval_baseline", "正解率")
    labels = [r["項目"].split(" ")[0] for r in new]
    fig, ax = plt.subplots(figsize=(9.5, 3.4))
    bars(ax, labels, {"素朴な方法（テキストの正規表現）": [pct(r["正解率"]) for r in base],
                      "PdfConditionReader（LLM＋ルール）": [pct(r["正解率"]) for r in new]}, [GREY, BLUE], ylim=(0, 112))
    ax.set_title("図面PDF：項目別の正解率（開発用 300枚）")
    save(fig, "pdf_fields")


def pdf_kinds():
    rows = table_with("pdf_eval_latest", "種類")
    labels = [r["種類"] for r in rows]
    fields = ["材質", "板厚", "数量", "表面処理", "追加加工", "特急"]
    fig, ax = plt.subplots(figsize=(9.5, 3.4))
    bars(ax, labels, {f: [pct(r[f]) for r in rows] for f in fields} | {"自動確定": [pct(r["自動確定"]) for r in rows]},
         ["#1f6aa5", "#2f8fcf", "#5aa6dd", "#8cc0e8", "#3c8c3c", "#6aa84f", ORANGE], ylim=(0, 115), width=0.86)
    ax.set_ylim(50, 108)
    ax.set_title("図面PDF：PDFの種類別の正解率と自動確定（開発用 300枚）")
    save(fig, "pdf_kinds")


def pdf_methods():
    rows = table_with("pdf_llm_comparison", "方式")
    labels = ["素朴な方法\n（LLMなし）", "LLM 1回読み\n＋ルール（初版）", "＋画像図面は\n2回読み", "最終版"]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.4))
    bars(axes[0], labels, {"価格項目がすべて正しい": [pct(r["価格項目がすべて正しい"]) for r in rows],
                           "自動確定": [pct(r["自動確定"]) for r in rows]}, [BLUE, GREEN], ylim=(0, 112), fmt="{:.1f}")
    axes[0].set_title("正しさと自動確定")
    danger = [pct(r["危険誤答を含む図面"]) for r in rows]
    axes[1].bar(labels, danger, color=[GREY, RED, RED, RED])
    for i, v in enumerate(danger):
        axes[1].text(i, v + 1, f"{v:g}%", ha="center", fontsize=8)
    axes[1].set_title("危険誤答を含む図面の割合")
    axes[1].set_ylabel("%")
    for ax in axes:
        ax.tick_params(axis="x", labelsize=7.5)
    save(fig, "pdf_methods")


def pdf_holdout_partial():
    rows = next(t for t in tables("pdf_eval_holdout") if t and "対象" in t[0])
    labels = [r["対象"] for r in rows]
    fields = ["材質", "板厚", "数量", "表面処理", "追加加工", "特急"]
    fig, ax = plt.subplots(figsize=(9.5, 3.3))
    bars(ax, labels, {f: [pct(r[f]) for r in rows] for f in fields},
         ["#1f6aa5", "#2f8fcf", "#5aa6dd", "#8cc0e8", "#3c8c3c", "#6aa84f"], ylim=(0, 115), width=0.86)
    ax.set_ylim(60, 108)
    ax.axhline(90, color=RED, lw=1, ls="--")
    ax.text(-0.45, 88.2, "種類別の条件 90%", color=RED, fontsize=8, ha="left", va="top")
    ax.set_title("参考：ホールドアウトで読み取れた148枚の正解率（判定は未完了）")
    ax.tick_params(axis="x", labelsize=8)
    save(fig, "pdf_holdout_partial")


# ---------------------------------------------------------------- similar quotes
def similar_quotes():
    rows = table_with("similar_quotes_eval", "方式")
    groups = []
    for r in rows:
        if r["対象"] not in groups:
            groups.append(r["対象"])
    auto = [pct(next(r["平均誤差"] for r in rows if r["対象"] == g and r["方式"].startswith("自動"))) for g in groups]
    sim = [pct(next(r["平均誤差"] for r in rows if r["対象"] == g and r["方式"].startswith("類似"))) for g in groups]
    fig, ax = plt.subplots(figsize=(8, 3.2))
    bars(ax, [g.replace("（前回の見積がある）", "\n（前回の見積がある）") for g in groups],
         {"自動見積（標準単価）だけ": auto, "類似見積を使う": sim}, [GREY, BLUE], ylim=(0, 14), fmt="{:.1f}%")
    ax.set_ylabel("平均誤差 %")
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    ax.set_title("単価の平均誤差（小さいほど実際の出し値に近い）")
    save(fig, "similar_quotes")


def main() -> int:
    for f in (step_before_after, step_datasets, step_stress, step_llm, dxf_compare, pdf_fields, pdf_kinds, pdf_methods,
              pdf_holdout_partial, similar_quotes):
        f()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
