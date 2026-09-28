"""Write example quotation PDFs (confirmed, estimate, internal basis) to analysis/. No LLM API.

    python scripts/make_quote_examples.py

The part is pdf_data/step/Lv2_0013.step, analysed by the rule-based analyzer. The conditions are entered by
hand (the estimate example simulates a drawing where the finish is not written and one process is not in the
master). Recipient and company are placeholders. The issue time is fixed, so the output is reproducible.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pypdfium2 as pdfium  # noqa: E402

from src.master_loader import MasterLoader  # noqa: E402
from src.models import AdditionalProcess, QuoteCondition  # noqa: E402
from src.pdf_quote import CONFIRMED, MISSING, UNREG, ConditionItem  # noqa: E402
from src.quote_document import PartInfo, Recipient, build_document, load_company  # noqa: E402
from src.quote_engine import QuoteEngine  # noqa: E402
from src.quote_pdf import render_internal, render_quote  # noqa: E402
from src.sheetmetal_analyzer import SheetMetalAnalyzer  # noqa: E402

STEP = ROOT / "pdf_data" / "step" / "Lv2_0013.step"
ISSUED = datetime(2026, 9, 28, 10, 0)
OUT = ROOT / "analysis"


def main() -> None:
    masters = MasterLoader(ROOT / "data")
    company = load_company(ROOT / "data")
    analysis = SheetMetalAnalyzer(k_factor=0.33, k_factor_is_default=False).analyze(STEP)
    analysis = analysis.model_copy(update={"file_name": "AB944-4829_bracket.step"})
    part = PartInfo(name="制御盤取付ブラケット", drawing_no="AB944-4829", revision="C", revision_date="2026.09.15",
                    shape_file=analysis.file_name, drawing_file="AB944-4829.pdf")
    recipient = Recipient("サンプル電機株式会社", "購買部　山田 太郎")
    processes = [AdditionalProcess(process_code=code, quantity=count, unit=masters.process(code)["unit"], source="drawing")
                 for code, count in (("TAP_M4", 6), ("TAP_M3", 8), ("PRESS_STUD", 5))]

    confirmed = QuoteCondition(material="AL5052", quantity=100, surface_treatment="ANODIZE_CLEAR",
                               additional_processes=processes)
    documents = {"quote_document_example.pdf": build_document(
        analysis=analysis, condition=confirmed, quote=QuoteEngine(masters).calculate(analysis, confirmed),
        masters=masters, company=company, recipient=recipient, part=part, issued_at=ISSUED, number="Q20260928-001")}

    items = [ConditionItem("material", "材質", "AL5052", "アルミ A5052P", CONFIRMED),
             ConditionItem("thickness_mm", "板厚", analysis.thickness_mm, f"{analysis.thickness_mm:g} mm", CONFIRMED),
             ConditionItem("quantity", "数量", 100, "100 個", CONFIRMED),
             ConditionItem("surface_treatment", "表面処理", None, "-", MISSING),
             ConditionItem("processes", "追加加工", [{"code": p.process_code, "count_per_part": p.quantity} for p in processes]
                           + [{"code": "UNREGISTERED", "count_per_part": None}], "M4タップ ×6 ほか", UNREG),
             ConditionItem("rush", "特急", False, "なし", CONFIRMED)]
    estimate = confirmed.model_copy(update={"surface_treatment": None,
                                            "pending": ["表面処理：記載なし", "追加加工：未登録"]})
    documents["quote_document_example_estimate.pdf"] = build_document(
        analysis=analysis, condition=estimate, quote=QuoteEngine(masters).calculate(analysis, estimate),
        masters=masters, company=company, recipient=recipient, part=part, issued_at=ISSUED, number="Q20260928-002",
        items=items, unregistered_texts=["バーリングタップ M4"])

    for name, document in documents.items():
        (OUT / name).write_bytes(render_quote(document))
        print(f"{name}: {document.title} 合計 {document.total:,} 円")
    (OUT / "quote_document_example_internal.pdf").write_bytes(render_internal(documents["quote_document_example_estimate.pdf"]))
    images = [pdfium.PdfDocument(OUT / name)[0].render(scale=1.4).to_pil() for name in documents]
    from PIL import Image

    sheet = Image.new("RGB", (sum(i.width for i in images) + 20, max(i.height for i in images)), "white")
    x = 0
    for image in images:
        sheet.paste(image, (x, 0))
        x += image.width + 20
    sheet.save(OUT / "quote_document_example.png", optimize=True)


if __name__ == "__main__":
    main()
