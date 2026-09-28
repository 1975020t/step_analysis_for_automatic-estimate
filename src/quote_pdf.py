"""Render a QuoteDocument as PDF (reportlab, A4 portrait, one page) with an embedded Japanese font.

The layout follows analysis/quote_document_sample.pdf. Long texts shrink, then wrap, to stay inside
their boxes. Output is byte-for-byte reproducible for the same document (reportlab invariant mode).
"""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from src.quote_document import QuoteDocument

FONT = "IPAGothic"
FONT_FILE = Path(__file__).resolve().parent.parent / "fonts" / "ipag.ttf"  # IPA Font License v1.0 (fonts/)
PAGE_W, PAGE_H = A4
LEFT, RIGHT = 15 * mm, 195 * mm
GREY = colors.Color(0.93, 0.93, 0.93)
RED = colors.Color(0.85, 0.1, 0.1)


def register_font() -> str:
    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(FONT, str(FONT_FILE)))
    return FONT


def width(text: str, size: float) -> float:
    return pdfmetrics.stringWidth(text, FONT, size)


def wrap(text: str, size: float, max_width: float) -> list[str]:
    """Break by character (Japanese has no spaces) so that every line fits max_width."""
    lines, current = [], ""
    for char in text:
        if current and width(current + char, size) > max_width:
            lines.append(current)
            current = char
        else:
            current += char
    return lines + [current] if current else lines or [""]


def text_in_box(c: canvas.Canvas, text: str, x: float, y_top: float, w: float, h: float, size: float,
                align: str = "left", pad: float = 1.5 * mm, color=colors.black) -> None:
    """One line, shrunk to fit; if it still does not fit at 70 %, wrapped (and shrunk) to the box height."""
    inner = w - 2 * pad
    s = size
    while width(text, s) > inner and s > size * 0.7:
        s -= 0.25
    lines = [text]
    if width(text, s) > inner:
        s = size * 0.85
        while True:
            lines = wrap(text, s, inner)
            if len(lines) * s * 1.2 <= h - 1 or s <= 4:
                break
            s -= 0.25
    leading = s * 1.2
    top = y_top - (h - leading * len(lines)) / 2 - s  # vertically centred
    c.setFillColor(color)
    c.setFont(FONT, s)
    for i, line in enumerate(lines):
        y = top - i * leading + s * 0.2
        if align == "right":
            c.drawRightString(x + w - pad, y, line)
        elif align == "center":
            c.drawCentredString(x + w / 2, y, line)
        else:
            c.drawString(x + pad, y, line)
    c.setFillColor(colors.black)


def yen(value: int) -> str:
    return f"{value:,}"


def render_quote(document: QuoteDocument) -> bytes:
    """The external quotation (見積書). No cost lines and no margin."""
    register_font()
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, invariant=1, initialFontName=FONT, lang="ja")
    c.setTitle(f"{document.title} {document.number}")
    c.setAuthor(document.company.name)
    c.setCreator("STEP板金解析・見積デモ")
    top = lambda y_mm: PAGE_H - y_mm * mm  # noqa: E731  (positions are measured from the top in mm)

    # title
    spacing = 7.0  # letter-spaced like the sample (御 見 積 書)
    text = c.beginText()
    text.setFont(FONT, 20)
    text.setCharSpace(spacing)
    text.setTextOrigin(104 * mm - (width(document.title, 20) + spacing * (len(document.title) - 1)) / 2, top(24))
    text.textOut(document.title)
    text.setCharSpace(0)  # the spacing would otherwise stay in the page's text state
    c.drawText(text)
    c.setLineWidth(0.8)
    c.line(62 * mm, top(28), 146 * mm, top(28))

    # number and dates (top right)
    c.setFont(FONT, 7.5)
    for i, (label, value) in enumerate((("見積番号", document.number),
                                        ("発行日", _date(document.issued_at)),
                                        ("有効期限", _date(document.valid_until)))):
        y = top(36 + i * 4.8)
        c.drawString(149 * mm, y, label)
        c.drawRightString(RIGHT, y, value)

    # recipient
    c.setLineWidth(0.6)
    text_in_box(c, f"{document.recipient.company}　御中", LEFT - 1.5 * mm, top(40), 94 * mm, 8 * mm, 12, pad=1.5 * mm)
    c.line(LEFT, top(48.5), 107 * mm, top(48.5))
    if document.recipient.person:
        text_in_box(c, f"{document.recipient.person} 様", LEFT - 1.5 * mm, top(50), 94 * mm, 6 * mm, 9)
    c.setFont(FONT, 8.5)
    c.drawString(LEFT, top(64), "下記のとおり御見積申し上げます。")

    # terms
    terms = (("件名", document.subject), ("納期", f"受注後 {document.lead_time_days}営業日"),
             ("受渡場所", document.delivery_place), ("取引条件", document.payment_terms),
             ("有効期限", f"発行日より{document.validity_days}日"))
    c.setLineWidth(0.5)
    for i, (label, value) in enumerate(terms):
        y0 = 67.5 + i * 6
        c.setFont(FONT, 7.5)
        c.drawString(LEFT, top(y0 + 4.2), label)
        text_in_box(c, value, 33.5 * mm, top(y0), 73.5 * mm, 6 * mm, 7.5)
        c.line(LEFT, top(y0 + 6), 107 * mm, top(y0 + 6))

    # company
    company = document.company
    text_in_box(c, company.name, 122.5 * mm, top(55.5), 70 * mm, 6 * mm, 10)
    rows = [f"〒{company.postal_code}" if company.postal_code else "", company.address,
            "　".join(x for x in (f"TEL {company.tel}" if company.tel else "", f"FAX {company.fax}" if company.fax else "") if x),
            f"登録番号 {company.registration_no}" if company.registration_no else "",
            f"担当：{company.contact}" if company.contact else ""]
    for i, row in enumerate(r for r in rows if r):
        text_in_box(c, row, 122.5 * mm, top(61.5 + i * 5.1), 72 * mm, 5 * mm, 7.2)
    # stamp boxes (frames only)
    c.setFont(FONT, 6)
    for i, label in enumerate(("承認", "担当")):
        x = 168.5 * mm + i * 13.25 * mm
        c.drawCentredString(x + 6.6 * mm, top(89.8), label)
        c.rect(x, top(106), 13.25 * mm, 15 * mm)

    # amount box
    c.setFillColor(GREY)
    c.rect(LEFT, top(116), 94 * mm, 12 * mm, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont(FONT, 9)
    c.drawString(LEFT + 3 * mm, top(111.3), "御見積金額（税込）")
    text_in_box(c, f"¥{yen(document.total)} -", 55 * mm, top(104), 52 * mm, 12 * mm, 16, align="right")

    # detail table
    columns = [15, 23.8, 73.4, 133, 143, 156.8, 175.6, 195]
    xs = [v * mm for v in columns]
    header_top, header_h = 124.0, 6.7
    rows_n = max(8, len(document.lines))
    row_h = min(10.0, 79.5 / rows_n)
    c.setFillColor(GREY)
    c.rect(xs[0], top(header_top + header_h), xs[-1] - xs[0], header_h * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    for i, label in enumerate(("No", "品名・図番", "仕様", "数量", "単位", "単価", "金額")):
        text_in_box(c, label, xs[i], top(header_top), xs[i + 1] - xs[i], header_h * mm, 7, align="center")
    table_bottom = header_top + header_h + rows_n * row_h
    c.setLineWidth(0.5)
    c.rect(xs[0], top(table_bottom), xs[-1] - xs[0], (table_bottom - header_top) * mm)
    c.setLineWidth(0.9)
    c.line(xs[0], top(header_top + header_h), xs[-1], top(header_top + header_h))
    c.setLineWidth(0.5)
    for x in xs[1:-1]:
        c.line(x, top(header_top), x, top(table_bottom))
    for r in range(1, rows_n):
        y = header_top + header_h + r * row_h
        c.line(xs[0], top(y), xs[-1], top(y))
    for r, line in enumerate(document.lines):
        y0 = header_top + header_h + r * row_h
        cell = lambda i: (xs[i], top(y0), xs[i + 1] - xs[i], row_h * mm)  # noqa: E731
        text_in_box(c, str(r + 1), *cell(0), 7, align="center")
        drawing = " ".join(x for x in (line.drawing_no, f"Rev.{line.revision}" if line.revision else "") if x)
        _two_lines(c, line.name, drawing, *cell(1), 7.2, 6.3)
        _two_lines(c, *(line.spec + ["", ""])[:2], *cell(2), 6.6, 6.6)
        text_in_box(c, f"{line.quantity:,}", *cell(3), 7.2, align="right")
        text_in_box(c, line.unit, *cell(4), 7.2, align="center")
        text_in_box(c, yen(line.unit_price), *cell(5), 7.2, align="right")
        text_in_box(c, yen(line.amount), *cell(6), 7.2, align="right")

    # totals
    tx = [142.3 * mm, 167 * mm, RIGHT]
    for i, (label, value) in enumerate((("小計", document.subtotal),
                                        (f"消費税（{document.tax_rate:.0%}）", document.tax),
                                        ("合計", document.total))):
        y0 = table_bottom + i * 7
        c.rect(tx[0], top(y0 + 7), tx[2] - tx[0], 7 * mm)
        c.line(tx[1], top(y0), tx[1], top(y0 + 7))
        text_in_box(c, label, tx[0], top(y0), tx[1] - tx[0], 7 * mm, 7.2, align="center")
        text_in_box(c, yen(value), tx[1], top(y0), tx[2] - tx[1], 7 * mm, 7.2, align="right")

    # remarks
    remarks_top = table_bottom + 3 * 7 + 7
    c.setFont(FONT, 8.5)
    c.drawString(LEFT, top(remarks_top), "備考")
    box_top = remarks_top + 3.5
    entries: list[tuple[str, object, float]] = []  # (text, colour, indent)
    if document.pending:
        entries.append(("・本見積は概算です。次の条件が未確定のため、確定後に金額が変わる場合があります。", RED, 0))
        entries += [(note, RED, 6 * mm) for note in document.pending]
    entries += [(f"・{remark}", colors.black, 0) for remark in document.remarks]
    available = 284 - 8 - box_top  # keep the page number free
    size = 7.2
    while True:
        leading = size * 1.55
        laid = [(line, color, indent) for text, color, indent in entries
                for line in wrap(text, size, RIGHT - LEFT - 6 * mm - indent)]
        height = len(laid) * leading + 3 * mm
        if height <= available * mm or size <= 4.5:
            break
        size -= 0.2
    box_h = max(18 * mm, height)
    c.rect(LEFT, top(box_top) - box_h, RIGHT - LEFT, box_h)
    y = top(box_top) - 1.5 * mm - size
    c.setFont(FONT, size)
    for line, color, indent in laid:
        c.setFillColor(color)
        c.drawString(LEFT + 2 * mm + indent, y, line)
        y -= leading
    c.setFillColor(colors.black)

    c.setFont(FONT, 7)
    c.drawCentredString(PAGE_W / 2, top(284), "1 / 1")
    c.showPage()
    c.save()
    return buffer.getvalue()


def _two_lines(c, first: str, second: str, x, y_top, w, h, size1, size2) -> None:
    if not second:
        text_in_box(c, first, x, y_top, w, h, size1)
        return
    text_in_box(c, first, x, y_top - 0.6 * mm, w, h / 2, size1)
    text_in_box(c, second, x, y_top - h / 2 + 0.2 * mm, w, h / 2, size2)


def _date(value) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def render_internal(document: QuoteDocument) -> bytes:
    """見積根拠（社内用、社外秘）: cost lines, subtotal, rush surcharge, margin, final price, the analysis
    values and state, and the drawing conditions with their states. A separate PDF from the quotation."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    register_font()
    basis = document.internal
    if basis is None:
        raise ValueError("社内用の見積根拠がありません。")
    quote, analysis, condition = basis.quote, basis.analysis, basis.condition
    buffer = io.BytesIO()
    title = f"見積根拠（社内用） {document.number}"
    pdf = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=15 * mm, title=title, author=document.company.name, invariant=1,
                            creator="STEP板金解析・見積デモ", initialFontName=FONT, lang="ja")
    h1 = ParagraphStyle("h1", fontName=FONT, fontSize=15, leading=20)
    h2 = ParagraphStyle("h2", fontName=FONT, fontSize=10, leading=14, spaceBefore=8, spaceAfter=3)
    body = ParagraphStyle("body", fontName=FONT, fontSize=8, leading=11)
    secret = ParagraphStyle("secret", fontName=FONT, fontSize=11, leading=14, textColor=RED)

    def table(rows, widths, right=()):
        data = [[Paragraph(_escape(str(v)), body) for v in row] for row in rows]
        t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1)
        style = [("FONTNAME", (0, 0), (-1, -1), FONT), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), GREY),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
        t.setStyle(TableStyle(style))
        for col in right:
            for row in data[1:]:
                row[col].style = ParagraphStyle("r", parent=body, alignment=2)
        return t

    summary = document.lines[0] if document.lines else None
    story = [Paragraph("【社外秘】", secret), Paragraph(_escape(title), h1),
             Paragraph(_escape(f"発行日 {_date(document.issued_at)}　宛先 {document.recipient.company}　件名 {document.subject}"
                               f"　見積の状態 {'概算' if document.is_estimate else '確定'}"), body),
             Paragraph("原価の内訳", h2)]
    rows = [["コード", "項目", "数量", "単位", "単価", "金額（円）"]]
    rows += [[line.code, line.name, _num(line.quantity), line.unit,
              "" if line.unit_price is None else _num(line.unit_price), f"{line.amount:,.1f}"] for line in quote.lines]
    story.append(table(rows, [26, 58, 22, 14, 22, 38], right=(2, 4, 5)))
    rush = next((line.amount for line in quote.lines if line.code == "RUSH"), 0.0)
    rows = [["項目", "値"],
            ["原価小計（特急割増を含む）", f"{quote.subtotal_cost:,.1f} 円"],
            ["うち特急割増", f"{rush:,.1f} 円"],
            ["粗利率", f"{quote.margin_rate:.0%}"],
            ["最終価格（QuoteEngine）", f"{quote.final_price:,.1f} 円"],
            ["数量", f"{condition.quantity:,} 個"]]
    if summary:
        rows += [["単価（1円未満切り上げ）", f"{summary.unit_price:,} 円"], ["金額", f"{summary.amount:,} 円"]]
    rows += [["小計", f"{document.subtotal:,} 円"], [f"消費税（{document.tax_rate:.0%}、1円未満切り捨て）", f"{document.tax:,} 円"],
             ["合計（税込）", f"{document.total:,} 円"]]
    story += [Paragraph("小計・割増・粗利・最終価格", h2), table(rows, [80, 100])]
    status = {"success": "確定", "partial": "概算"}.get(analysis.status, analysis.status)
    rows = [["項目", "値", "信頼度"]]
    for name, label, fmt in (("thickness_mm", "板厚", "{:g} mm"), ("blank_area_mm2", "展開面積", "{:,.1f} mm²"),
                             ("cut_length_mm", "切断長", "{:,.1f} mm"), ("hole_count", "穴数", "{}"),
                             ("bend_count", "曲げ数", "{}")):
        value = getattr(analysis, name)
        quality = analysis.metric_quality.get(name)
        rows.append([label, "-" if value is None else fmt.format(value), quality.confidence if quality else "-"])
    story += [Paragraph(_escape(f"解析値（{analysis.file_name}、解析の状態：{status}）"), h2), table(rows, [40, 80, 60])]
    notes = list(analysis.assumptions) + list(analysis.warnings)
    if notes:
        story += [Spacer(1, 3)] + [Paragraph(_escape("・" + n), body) for n in notes]
    if basis.items:
        rows = [["項目", "読み取り値", "状態", "理由"]]
        rows += [[i.label, i.display, i.status, "、".join(i.reasons)] for i in basis.items]
        story += [Paragraph("図面から読み取った条件", h2), table(rows, [22, 70, 20, 68])]
    if document.pending:
        story += [Paragraph("未確定の条件（見積書の備考に記載）", h2)] + [Paragraph(_escape("・" + n), body) for n in document.pending]
    pdf.build(story)
    return buffer.getvalue()


def _num(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.2f}"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
