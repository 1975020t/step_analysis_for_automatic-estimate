"""Similar drawings and similar past quotes with ONE rule (src/similar_quotes.is_similar): same material, bends
within 1, holes within 2, smallest difference first. The drawing list (「類似形状検索」), the drawing detail and the
estimate screen (「類似実績」) all call `similar`, so the same part gives the same results everywhere.

The pool: the registered drawings (their material and the bends / holes of their shape analysis, with their
latest quote) and the past quotes of the history that are not tied to a registered drawing. Drawings without a
shape file (unknown bends / holes) are not in it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.db.models import Drawing, PastQuoteRow
from src.services.platform.core import Platform, iso
from src.services.platform.drawings import latest_quotes, metrics_of
from src.similar_quotes import difference, is_similar

LIMIT = 30


def similar(pf: Platform, material: str | None, bends: int | None, holes: int | None,
            exclude_drawing_id: int | None = None, limit: int = LIMIT) -> list[dict]:
    if not material or bends is None or holes is None:
        return []
    masters = pf.masters()
    found: list[tuple[tuple, dict]] = []
    with pf.session() as s:
        drawings = list(s.scalars(select(Drawing).options(selectinload(Drawing.revisions))))
        latest = latest_quotes(s, [d.id for d in drawings])
        for d in drawings:
            if d.id == exclude_drawing_id:
                continue
            revision = next((r for r in d.revisions if r.id == d.current_revision_id), None)
            m = metrics_of(revision)
            if revision is None or not is_similar(material, bends, holes, revision.material, m["bend_count"], m["hole_count"]):
                continue
            q = latest.get(d.id)
            price = (q.result or {}).get("price") if q else None
            item = {"kind": "drawing", "drawing_id": d.id, "drawing_no": d.drawing_no, "revision": revision.revision,
                    "name": d.name, "customer": d.customer, "material": revision.material,
                    "bends": m["bend_count"], "holes": m["hole_count"], "thickness_mm": m["thickness_mm"],
                    "quantity": (q.inputs or {}).get("quantity") if q else None,
                    "unit_price": price["unit_price"] if price else None,
                    "outcome": q.case.outcome if q else None, "status": q.case.status.name if q else "未作成",
                    "date": iso((q.updated_at if q else d.created_at).date()), "quote_no": q.number if q else "",
                    "has_pdf": bool(revision.pdf_file_id), "revision_id": revision.id}
            found.append((_key(bends, holes, item), item))
        rows = s.scalars(select(PastQuoteRow).where(PastQuoteRow.drawing_id.is_(None),
                                                     PastQuoteRow.material_code == material,
                                                     PastQuoteRow.bends.is_not(None), PastQuoteRow.holes.is_not(None)))
        for r in rows:
            if not is_similar(material, bends, holes, r.material_code, r.bends, r.holes):
                continue
            item = {"kind": "quote", "drawing_id": None, "drawing_no": r.drawing_no, "revision": r.revision,
                    "name": r.part_name, "customer": r.customer, "material": r.material_code, "bends": r.bends,
                    "holes": r.holes, "thickness_mm": r.thickness, "quantity": r.quantity, "unit_price": r.unit_price,
                    "outcome": r.outcome, "status": r.outcome, "date": iso(r.date), "quote_no": r.quote_no,
                    "has_pdf": False, "revision_id": None}
            found.append((_key(bends, holes, item), item))
    found.sort(key=lambda kv: kv[0])
    out = []
    for _, item in found[:limit]:
        db, dh = abs(item["bends"] - bends), abs(item["holes"] - holes)
        item["bend_diff"], item["hole_diff"] = item["bends"] - bends, item["holes"] - holes
        item["difference"] = "条件が同じ" if not (db or dh) else "・".join(
            x for x in (f"曲げ {item['bends'] - bends:+d}" if db else "", f"穴 {item['holes'] - holes:+d}" if dh else "") if x)
        item["material_name"] = masters.materials.get(item["material"], {}).get("display_name", item["material"])
        out.append(item)
    return out


def _key(bends: int, holes: int, item: dict) -> tuple:
    return (*difference(bends, holes, item["bends"], item["holes"]), "" if not item["date"] else
            "".join(chr(255 - ord(c)) for c in item["date"]), item["drawing_no"])
