"""納品書 and 請求書 (A4, one page) for an accepted quotation, with the amounts of the issued quotation.

The 請求書 is a qualified invoice (適格請求書): the issuer's registration number, the tax rate and the total
per tax rate. Same font and helpers as the quotation (src/quote_pdf.py). No LLM, no calculation here: the
amounts are those of the issued quotation (src/services/platform/documents.py passes them).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date, datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from src.quote_document import Company
from src.quote_pdf import FONT, GREY, LEFT, PAGE_H, PAGE_W, RIGHT, register_font, text_in_box, width, wrap, yen


@dataclass
class TradeLine:
    name: str
    drawing_no: str = ""
    revision: str = ""
    spec: str = ""
    quantity: int = 1
    unit: str = "個"
    unit_price: int = 0
    amount: int = 0


@dataclass
class TradeDocument:
    kind: str                      # delivery / invoice
    number: str
    issued_at: datetime
    recipient: str
    person: str
    subject: str
    quote_no: str
    company: Company
    lines: list[TradeLine]
    tax_rate: float
    subtotal: int
    tax: int
    total: int
    remarks: list[str] = field(default_factory=list)
    due_date: date | None = None   # invoice: payment due; delivery: delivered on (issued date)
    options: dict = field(default_factory=dict)

    @property
    def title(self) -> str:
        return "納品書" if self.kind == "delivery" else "請求書"


def _d(value) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def render_trade(doc: TradeDocument) -> bytes:
    opts = {"drawing_no": True, "unit_and_quantity": True, "remarks": True, "seal": True, **(doc.options or {})}
    register_font()
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, invariant=1, initialFontName=FONT, lang="ja")
    c.setTitle(f"{doc.title} {doc.number}")
    c.setAuthor(doc.company.name)
    top = lambda y_mm: PAGE_H - y_mm * mm  # noqa: E731

    spacing = 7.0
    text = c.beginText()
    text.setFont(FONT, 20)
    text.setCharSpace(spacing)
    text.setTextOrigin(104 * mm - (width(doc.title, 20) + spacing * (len(doc.title) - 1)) / 2, top(24))
    text.textOut(doc.title)
    text.setCharSpace(0)
    c.drawText(text)
    c.setLineWidth(0.8)
    c.line(70 * mm, top(28), 138 * mm, top(28))

    c.setFont(FONT, 7.5)
    head = [("番号", doc.number), ("発行日", _d(doc.issued_at)), ("見積番号", doc.quote_no)]
    for i, (label, value) in enumerate(head):
        y = top(36 + i * 4.8)
        c.drawString(149 * mm, y, label)
        c.drawRightString(RIGHT, y, value)

    c.setLineWidth(0.6)
    text_in_box(c, f"{doc.recipient}　御中", LEFT - 1.5 * mm, top(40), 94 * mm, 8 * mm, 12)
    c.line(LEFT, top(48.5), 107 * mm, top(48.5))
    if doc.person:
        text_in_box(c, f"{doc.person} 様", LEFT - 1.5 * mm, top(50), 94 * mm, 6 * mm, 9)
    c.setFont(FONT, 8.5)
    c.drawString(LEFT, top(64), "下記のとおり納品いたします。" if doc.kind == "delivery" else "下記のとおりご請求申し上げます。")
    c.setFont(FONT, 7.5)
    c.drawString(LEFT, top(70), f"件名　{doc.subject}")
    if doc.kind == "invoice" and doc.due_date:
        c.drawString(LEFT, top(75), f"お支払期限　{_d(doc.due_date)}　（{doc.company.payment_terms}）")

    company = doc.company
    text_in_box(c, company.name, 122.5 * mm, top(55.5), 70 * mm, 6 * mm, 10)
    rows = [f"〒{company.postal_code}" if company.postal_code else "", company.address,
            "　".join(x for x in (f"TEL {company.tel}" if company.tel else "", f"FAX {company.fax}" if company.fax else "") if x),
            f"登録番号 {company.registration_no}" if company.registration_no else "",
            f"担当：{company.contact}" if company.contact else ""]
    for i, row in enumerate(r for r in rows if r):
        text_in_box(c, row, 122.5 * mm, top(61.5 + i * 5.1), 72 * mm, 5 * mm, 7.2)
    if opts["seal"]:
        c.setFont(FONT, 6)
        x = 181.75 * mm
        c.drawCentredString(x + 6.6 * mm, top(89.8), "印")
        c.rect(x, top(106), 13.25 * mm, 15 * mm)

    c.setFillColor(GREY)
    c.rect(LEFT, top(98), 94 * mm, 12 * mm, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont(FONT, 9)
    c.drawString(LEFT + 3 * mm, top(93.3), "納品金額（税込）" if doc.kind == "delivery" else "ご請求金額（税込）")
    text_in_box(c, f"¥{yen(doc.total)} -", 55 * mm, top(86), 52 * mm, 12 * mm, 16, align="right")

    columns = [15, 23.8, 100, 133, 143, 165, 195]
    xs = [v * mm for v in columns]
    header_top, header_h, row_h, rows_n = 112.0, 6.7, 9.0, 8
    c.setFillColor(GREY)
    c.rect(xs[0], top(header_top + header_h), xs[-1] - xs[0], header_h * mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    for i, label in enumerate(("No", "品名・図番", "数量", "単位", "単価", "金額")):
        text_in_box(c, label, xs[i], top(header_top), xs[i + 1] - xs[i], header_h * mm, 7, align="center")
    bottom = header_top + header_h + rows_n * row_h
    c.setLineWidth(0.5)
    c.rect(xs[0], top(bottom), xs[-1] - xs[0], (bottom - header_top) * mm)
    for x in xs[1:-1]:
        c.line(x, top(header_top), x, top(bottom))
    for r in range(1, rows_n):
        y = header_top + header_h + r * row_h
        c.line(xs[0], top(y), xs[-1], top(y))
    for r, line in enumerate(doc.lines):
        y0 = header_top + header_h + r * row_h
        cell = lambda i: (xs[i], top(y0), xs[i + 1] - xs[i], row_h * mm)  # noqa: E731
        text_in_box(c, str(r + 1), *cell(0), 7, align="center")
        drawing = " ".join(x for x in (line.drawing_no, f"Rev.{line.revision}" if line.revision else "") if x)
        name = line.name + (f"　{drawing}" if drawing and opts["drawing_no"] else "")
        text_in_box(c, name, *cell(1), 7.2)
        if opts["unit_and_quantity"]:
            text_in_box(c, f"{line.quantity:,}", *cell(2), 7.2, align="right")
            text_in_box(c, line.unit, *cell(3), 7.2, align="center")
            text_in_box(c, yen(line.unit_price), *cell(4), 7.2, align="right")
        text_in_box(c, yen(line.amount), *cell(5), 7.2, align="right")

    rate = f"{doc.tax_rate:.0%}"
    totals = [("小計（税抜）", doc.subtotal), (f"消費税（{rate}）", doc.tax), ("合計（税込）", doc.total)]
    tx = [135 * mm, 165 * mm, RIGHT]
    for i, (label, value) in enumerate(totals):
        y0 = bottom + i * 7
        c.rect(tx[0], top(y0 + 7), tx[2] - tx[0], 7 * mm)
        c.line(tx[1], top(y0), tx[1], top(y0 + 7))
        text_in_box(c, label, tx[0], top(y0), tx[1] - tx[0], 7 * mm, 7.2, align="center")
        text_in_box(c, yen(value), tx[1], top(y0), tx[2] - tx[1], 7 * mm, 7.2, align="right")
    y_after = bottom + 3 * 7 + 6
    if doc.kind == "invoice":  # 適格請求書: the total and the tax per tax rate
        c.setFont(FONT, 8)
        c.drawString(LEFT, top(y_after), "税率別内訳")
        widths = [15, 60, 105, 150, 195]
        heads = ["税率", "対象金額（税抜）", "消費税", "合計（税込）"]
        values = [f"{rate}対象", f"¥{yen(doc.subtotal)}", f"¥{yen(doc.tax)}", f"¥{yen(doc.total)}"]
        for row, cells in enumerate((heads, values)):
            for i, value in enumerate(cells):
                x0, x1 = widths[i] * mm, widths[i + 1] * mm
                y0 = y_after + 2 + row * 7
                c.rect(x0, top(y0 + 7), x1 - x0, 7 * mm)
                text_in_box(c, value, x0, top(y0), x1 - x0, 7 * mm, 7.2, align="center" if row == 0 or i == 0 else "right")
        c.setFont(FONT, 7)
        reg = company.registration_no or "（未設定）"
        c.drawString(LEFT, top(y_after + 21), f"適格請求書発行事業者 登録番号：{reg}")
        y_after += 26
    if opts["remarks"] and doc.remarks:
        c.setFont(FONT, 8.5)
        c.drawString(LEFT, top(y_after + 2), "備考")
        y = y_after + 7
        c.setFont(FONT, 7.2)
        for remark in doc.remarks:
            for line in wrap(f"・{remark}", 7.2, RIGHT - LEFT - 4 * mm):
                c.drawString(LEFT + 2 * mm, top(y), line)
                y += 4.2
    c.setFont(FONT, 7)
    c.drawCentredString(PAGE_W / 2, top(284), "1 / 1")
    c.showPage()
    c.save()
    return buffer.getvalue()
