"""Quote history: the past quotes and the quotes issued by this app, kept as a CSV file in the repository.

History file (data/past_quotes/history.csv, UTF-8, one quote per line, Git-diffable):
  text columns keep what was written (material_text, finish_text, processes_text); the *_code columns hold
  the master code the text maps to, "UNREGISTERED" (a concrete name that is not in the master) or blank.
  material_family is the family (steel / stainless / aluminum / copper) even when only the family is known
  ("SUS", "AL"). processes is JSON: [{"code", "count", "text"}]. original is JSON: the imported row as it was.

Import (`import_csv`): a CSV exported from Excel; columns are matched by name (COLUMN_ALIASES, or an explicit
mapping), missing columns are left blank. Text is mapped with the master aliases (MasterLoader.resolve_alias)
and the drawing-reading rules (src/pdf_terms.py). No LLM.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from pathlib import Path

from src.master_loader import UNREGISTERED, MasterLoader, normalize_term

HISTORY_PATH = Path("data/past_quotes/history.csv")
OUTCOMES = ("受注", "失注", "未回答")
IGNORED_COLUMNS = {"出典"}  # test-only provenance of the seed data: never stored, searched or shown

# shop-floor shorthand for materials that the master aliases do not carry (the family is certain, the grade is
# the usual one): kept here so the master and the drawing reader are unchanged
MATERIAL_SHORTHAND = {"冷延": "SPCC", "ボンデ": "SECC", "酸洗": "SPHC"}
FAMILY_LABEL = {"steel": "鉄", "stainless": "ステンレス", "aluminum": "アルミ", "copper": "銅"}

COLUMN_ALIASES: dict[str, list[str]] = {
    "quote_no": ["見積番号", "見積No", "見積NO", "見積No.", "quote_no"],
    "date": ["見積日", "日付", "発行日", "date"],
    "customer": ["顧客名", "顧客", "得意先", "得意先名", "取引先", "customer"],
    "drawing_no": ["図番", "図面番号", "drawing_no"],
    "revision": ["改訂", "改訂記号", "Rev", "REV", "revision"],
    "part_name": ["品名", "部品名", "part_name"],
    "material_text": ["材質", "材料", "material"],
    "thickness": ["板厚", "板厚_mm", "t", "thickness"],
    "quantity": ["数量", "個数", "quantity"],
    "finish_text": ["表面処理", "処理", "finish"],
    "processes_text": ["追加加工", "加工", "processes"],
    "rush": ["特急", "rush"],
    "unit_price": ["単価", "unit_price"],
    "amount": ["金額", "amount"],
    "outcome": ["結果", "受注結果", "outcome"],
    "staff": ["担当者", "担当", "staff"],
    "remarks": ["備考", "remarks"],
    "area": ["展開面積_mm2", "展開面積", "area"],
    "cut": ["切断長_mm", "切断長", "cut"],
    "holes": ["穴数", "holes"],
    "bends": ["曲げ数", "bends"],
    "flat_size": ["展開寸法_mm", "展開寸法", "flat_size"],
    "shape_class": ["形状区分", "shape_class"],
}


@dataclass
class PastQuote:
    quote_no: str
    date: date
    customer: str
    drawing_no: str = ""
    revision: str = ""
    part_name: str = ""
    material_text: str = ""
    material_code: str = ""       # master code, UNREGISTERED, or "" (blank / only the family is known)
    material_family: str = ""     # steel / stainless / aluminum / copper, "" if unknown
    thickness: float | None = None
    quantity: int | None = None
    finish_text: str = ""
    finish_code: str = ""         # master code (NONE = no finish), UNREGISTERED, or "" (not recorded)
    processes_text: str = ""
    processes: list[dict] = field(default_factory=list)  # [{"code", "count", "text"}]
    rush: bool = False
    unit_price: int | None = None
    amount: int | None = None
    outcome: str = "未回答"
    staff: str = ""
    remarks: str = ""
    area: float | None = None
    cut: float | None = None
    holes: int | None = None
    bends: int | None = None
    flat_size: str = ""
    shape_class: str = ""
    source: str = ""              # "import:<file name>" or "app"
    original: dict = field(default_factory=dict)

    @property
    def has_shape(self) -> bool:
        return None not in (self.area, self.cut, self.holes, self.bends)


FIELDS = [f.name for f in fields(PastQuote)]


# ---------------------------------------------------------------- text -> codes
def material_family(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").upper().replace(" ", "")
    if not t:
        return ""
    if t.startswith("SUS") or "ステンレス" in t or "STAINLESS" in t:
        return "stainless"
    if re.match(r"^(A\d{4}|AL)", t) or "アルミ" in t or "ALUMINUM" in t or "ALUMINIUM" in t:
        return "aluminum"
    if re.match(r"^C\d{4}", t) or "銅" in t or "COPPER" in t or "BRASS" in t:
        return "copper"
    if re.match(r"^(SPC|SPH|SEC|SGC|SS|SAPH|SPFC|STKM)", t) or any(w in t for w in ("冷延", "酸洗", "ボンデ", "鉄", "鋼", "STEEL")):
        return "steel"
    return ""


class TermMapper:
    """Maps written material / finish / process text to master codes (aliases first, then the reading rules)."""

    def __init__(self, masters: MasterLoader) -> None:
        from src.pdf_terms import Terms

        self.masters = masters
        self.terms = Terms(masters)

    def material(self, text: str) -> tuple[str, str]:
        text = (text or "").strip()
        if not text:
            return "", ""
        code = (self.masters.resolve_alias("material", text) or MATERIAL_SHORTHAND.get(normalize_term(text))
                or self.terms.material(text) or "")
        family = material_family(code if code and code != UNREGISTERED else text) or material_family(text)
        return code, family

    def finish(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        return self.masters.resolve_alias("surface_treatment", text) or self.terms.finish(text) or UNREGISTERED

    def processes(self, text: str) -> list[dict]:
        out = []
        for token in re.split(r"[、,，;；/／\n]+", text or ""):
            token = token.strip()
            if not token:
                continue
            parsed = self.terms.process(token)
            if parsed == []:
                continue  # a plain hole or a note: not an additional process
            if not parsed:
                count = re.search(r"(\d+)\s*(?:ヶ所|箇所|か所|個|点)|[×xX]\s*(\d+)\s*$", token)
                parsed = [(UNREGISTERED, int(next(g for g in count.groups() if g)) if count else None)]
            for code, count in parsed:
                out.append({"code": code, "count": count, "text": token})
        return out


# ---------------------------------------------------------------- import
def _number(text) -> float | None:
    t = unicodedata.normalize("NFKC", str(text or "")).replace(",", "").replace("¥", "").replace("円", "").strip()
    try:
        return float(t) if t else None
    except ValueError:
        return None


def _int(text) -> int | None:
    value = _number(text)
    return int(round(value)) if value is not None else None


def _date(text) -> date | None:
    t = unicodedata.normalize("NFKC", str(text or "")).strip()
    for pattern in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y/%m/%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(t, pattern).date()
        except ValueError:
            pass
    return None


def _rush(text) -> bool:
    t = unicodedata.normalize("NFKC", str(text or "")).strip().upper()
    return t not in ("", "0", "FALSE", "NO", "なし", "無", "×", "-", "―")


def _outcome(text) -> str:
    t = (text or "").strip()
    if t in ("受注", "受注済", "成約", "WON", "won"):
        return "受注"
    if t in ("失注", "不成約", "LOST", "lost"):
        return "失注"
    return "未回答"


def column_map(header: list[str], mapping: dict[str, str] | None = None) -> dict[str, str]:
    """field -> CSV column. An explicit mapping wins; otherwise the first alias found in the header."""
    mapping = dict(mapping or {})
    unknown = [f for f in mapping if f not in COLUMN_ALIASES]
    if unknown:
        raise ValueError(f"未知の項目名です: {unknown}（使える項目: {list(COLUMN_ALIASES)}）")
    missing = [c for c in mapping.values() if c not in header]
    if missing:
        raise ValueError(f"CSVに列がありません: {missing}")
    normalized = {unicodedata.normalize("NFKC", h).strip().upper(): h for h in header}
    for name, aliases in COLUMN_ALIASES.items():
        if name in mapping:
            continue
        for alias in aliases:
            column = normalized.get(unicodedata.normalize("NFKC", alias).strip().upper())
            if column is not None:
                mapping[name] = column
                break
    return mapping


def read_csv_rows(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp932"):  # Excel on Windows writes Shift_JIS (cp932) unless told otherwise
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("CSVの文字コードを判別できません（UTF-8 か Shift_JIS で保存してください）。")
    reader = csv.DictReader(text.splitlines())
    return list(reader.fieldnames or []), list(reader)


def import_csv(path: str | Path, masters: MasterLoader, mapping: dict[str, str] | None = None,
               mapper: TermMapper | None = None) -> tuple[list[PastQuote], list[str]]:
    """Rows of a past-quote CSV as PastQuote, and the problems found (rows skipped and why)."""
    header, rows = read_csv_rows(path)
    columns = column_map(header, mapping)
    mapper = mapper or TermMapper(masters)
    name = Path(path).name
    quotes, problems = [], []
    for number, row in enumerate(rows, start=2):
        get = lambda f: (row.get(columns[f]) or "").strip() if f in columns else ""  # noqa: E731
        day = _date(get("date"))
        if day is None or not get("customer"):
            problems.append(f"{number}行目: 見積日か顧客名がないため取り込みません")
            continue
        material_code, family = mapper.material(get("material_text"))
        quantity = _int(get("quantity"))
        unit = _int(get("unit_price"))
        amount = _int(get("amount"))
        if unit is None and amount is not None and quantity:
            unit = round(amount / quantity)
        quotes.append(PastQuote(
            quote_no=get("quote_no") or f"{name}:{number}", date=day, customer=get("customer"),
            drawing_no=get("drawing_no"), revision=get("revision"), part_name=get("part_name"),
            material_text=get("material_text"), material_code=material_code, material_family=family,
            thickness=_number(get("thickness").lower().lstrip("t")), quantity=quantity,
            finish_text=get("finish_text"), finish_code=mapper.finish(get("finish_text")),
            processes_text=get("processes_text"), processes=mapper.processes(get("processes_text")),
            rush=_rush(get("rush")), unit_price=unit, amount=amount if amount is not None else (unit * quantity if unit and quantity else None),
            outcome=_outcome(get("outcome")), staff=get("staff"), remarks=get("remarks"),
            area=_number(get("area")), cut=_number(get("cut")), holes=_int(get("holes")), bends=_int(get("bends")),
            flat_size=get("flat_size"), shape_class=get("shape_class"), source=f"import:{name}",
            original={k: v for k, v in row.items() if k and k not in IGNORED_COLUMNS}))
    return quotes, problems


# ---------------------------------------------------------------- storage
def _cell(name: str, value) -> str:
    if value is None:
        return ""
    if name in ("processes", "original"):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "1" if value else ""
    if isinstance(value, float):
        return f"{value:g}" if name == "thickness" else (f"{value:.1f}" if value != int(value) else str(int(value)))
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _from_row(row: dict[str, str]) -> PastQuote:
    return PastQuote(
        quote_no=row["quote_no"], date=date.fromisoformat(row["date"]), customer=row["customer"],
        drawing_no=row.get("drawing_no", ""), revision=row.get("revision", ""), part_name=row.get("part_name", ""),
        material_text=row.get("material_text", ""), material_code=row.get("material_code", ""),
        material_family=row.get("material_family", ""), thickness=_number(row.get("thickness")),
        quantity=_int(row.get("quantity")), finish_text=row.get("finish_text", ""), finish_code=row.get("finish_code", ""),
        processes_text=row.get("processes_text", ""), processes=json.loads(row.get("processes") or "[]"),
        rush=row.get("rush") == "1", unit_price=_int(row.get("unit_price")), amount=_int(row.get("amount")),
        outcome=row.get("outcome") or "未回答", staff=row.get("staff", ""), remarks=row.get("remarks", ""),
        area=_number(row.get("area")), cut=_number(row.get("cut")), holes=_int(row.get("holes")),
        bends=_int(row.get("bends")), flat_size=row.get("flat_size", ""), shape_class=row.get("shape_class", ""),
        source=row.get("source", ""), original=json.loads(row.get("original") or "{}"))


class HistoryStore:
    """The history CSV. Rows keep their order (oldest first); outcome updates rewrite the file in place."""

    def __init__(self, path: str | Path = HISTORY_PATH) -> None:
        self.path = Path(path)

    def load(self) -> list[PastQuote]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8", newline="") as handle:
            return [_from_row(row) for row in csv.DictReader(handle)]

    def save(self, quotes: list[PastQuote]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            for quote in quotes:
                writer.writerow({name: _cell(name, value) for name, value in asdict(quote).items()})
        tmp.replace(self.path)

    def append(self, new: list[PastQuote]) -> tuple[int, int]:
        """Add quotes; one already in the history (same number, date and customer) is skipped.
        Returns (added, skipped)."""
        quotes = self.load()
        key = lambda q: (q.quote_no, q.date, q.customer)  # noqa: E731  (numbering differs between sources)
        known = {key(q) for q in quotes}
        added = [q for q in new if key(q) not in known]
        if added:
            self.save(sorted(quotes + added, key=lambda q: (q.date, q.quote_no)))
        return len(added), len(new) - len(added)

    def set_outcome(self, quote_no: str, outcome: str, customer: str | None = None) -> None:
        if outcome not in OUTCOMES:
            raise ValueError(f"結果は {OUTCOMES} のどれかです: {outcome}")
        quotes = self.load()
        for quote in quotes:
            if quote.quote_no == quote_no and (customer is None or quote.customer == customer):
                quote.outcome = outcome
                break
        else:
            raise KeyError(f"履歴にない見積番号です: {quote_no}")
        self.save(quotes)


def from_document(document, masters: MasterLoader) -> PastQuote:
    """History row for an issued quotation (src/quote_document.QuoteDocument): searched from the next quote on."""
    basis = document.internal
    analysis, condition = basis.analysis, basis.condition
    line = document.lines[0]
    finish_code = condition.surface_treatment or "NONE"
    finish_text = (masters.surface_treatments[condition.surface_treatment]["display_name"]
                   if condition.surface_treatment else "なし")
    processes = [{"code": p.process_code, "count": p.quantity, "text": f"{masters.process_rates[p.process_code]['display_name']}×{p.quantity:g}"}
                 for p in condition.additional_processes if p.confirmed and p.process_code in masters.process_rates]
    return PastQuote(
        quote_no=document.number, date=document.issued_at.date(), customer=document.recipient.company,
        drawing_no=document.part.drawing_no, revision=document.part.revision, part_name=line.name,
        material_text=condition.material, material_code=condition.material, material_family=material_family(condition.material),
        thickness=analysis.thickness_mm, quantity=line.quantity, finish_text=finish_text, finish_code=finish_code,
        processes_text="、".join(p["text"] for p in processes), processes=processes, rush=condition.rush,
        unit_price=line.unit_price, amount=line.amount, outcome="未回答", staff=document.company.contact,
        remarks="",
        area=analysis.blank_area_mm2, cut=analysis.cut_length_mm, holes=analysis.hole_count, bends=analysis.bend_count,
        flat_size="", shape_class="", source="app",
        original={"件名": document.subject, "宛先": document.recipient.company, "宛先担当": document.recipient.person,
                  "発行日時": document.issued_at.isoformat(timespec="seconds"), "合計（税込）": document.total,
                  "入力ファイル": " | ".join(f for f in (document.part.shape_file, document.part.drawing_file) if f)})
