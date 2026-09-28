"""The first thing one would write: extract the PDF's text layer and match keywords / master aliases.

For comparison only - not production code. It shows what the text layer alone gives: nothing for
scans and FAXes (image only), and on CAD-exported PDFs it has no notion of revisions, parts-list
semantics, distractors or "not written". It never asks for a review.

    python scripts/evaluate_pdf.py --data pdf_data --reader golden.pdf_naive_baseline:NaiveTextReader
"""
from __future__ import annotations

import re
import unicodedata

from src.master_loader import UNREGISTERED, MasterLoader, normalize_term

FLAG_WORDS = {"tolerance": ["公差 ±", "平面度", "TOLERANCE ±", "FLATNESS"], "appearance": ["外観", "化粧面", "COSMETIC"],
              "inspection": ["検査", "ミルシート", "INSPECTION", "CERTIFICATE"]}


def pdf_text(path) -> str:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    try:
        return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    finally:
        doc.close()


class NaiveTextReader:
    def __init__(self, data_dir="data"):
        self.masters = MasterLoader(data_dir)
        self.terms = {kind: sorted(((normalize_term(t), code) for code, row in rows.items()
                                    for t in [code, *self.masters.aliases_of(row)] if normalize_term(t)),
                                   key=lambda x: -len(x[0]))
                      for kind, rows in (("material", self.masters.materials), ("surface_treatment", self.masters.surface_treatments))}

    def _first_alias(self, kind, text):
        flat = normalize_term(text)
        hits = [(flat.find(term), code) for term, code in self.terms[kind] if term in flat]
        return min(hits)[1] if hits else None

    def read(self, path) -> dict:
        text = unicodedata.normalize("NFKC", pdf_text(path))
        out = {"drawing_no": None, "revision": None, "material": None, "thickness_mm": None, "quantity": None,
               "surface_treatment": None, "processes": [], "rush": False, "flags": [], "needs_review": []}
        if not text.strip():
            return out  # image-only PDF: nothing to read
        out["material"] = self._first_alias("material", text)
        out["surface_treatment"] = self._first_alias("surface_treatment", text)
        m = re.search(r"(?:板厚|厚さ|THK|THICKNESS|\bt)\s*[=:]?\s*(\d+(?:\.\d+)?)", text) or re.search(r"(\d+\.\d)\s*t\b", text)
        if m:
            out["thickness_mm"] = float(m.group(1))
        m = re.search(r"(?:数量|個数|製作数|手配数|QTY|QUANTITY)\s*[:：]?\s*(?:N=)?([\d,]+)", text)
        if m:
            out["quantity"] = int(m.group(1).replace(",", ""))
        taps = {}
        for n, d in re.findall(r"(\d+)\s*[-X×]\s*M(\d)\b", text):
            taps[f"TAP_M{d}"] = taps.get(f"TAP_M{d}", 0) + int(n)
        for d, n in re.findall(r"M(\d)\s*タップ\s*(\d+)", text):
            taps[f"TAP_M{d}"] = taps.get(f"TAP_M{d}", 0) + int(n)
        out["processes"] = [{"code": c, "count_per_part": n} for c, n in taps.items() if c in self.masters.process_rates]
        out["rush"] = bool(re.search(r"特急|至急|急ぎ|URGENT|RUSH", text))
        out["flags"] = [k for k, words in FLAG_WORDS.items() if any(w in text for w in words)]
        m = re.search(r"(?:図番|図面番号|DRAWING NO\.|DWG NO\.)\s*[:：]?\s*([A-Z0-9][A-Z0-9\-]+)", text)
        if m:
            out["drawing_no"] = m.group(1)
        return out


__all__ = ["NaiveTextReader", "UNREGISTERED"]
