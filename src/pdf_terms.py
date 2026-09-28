"""Deterministic interpretation of condition texts read from drawings (material, finish, thickness,
quantity, additional processes, rush, special requirements).

The LLM transcribes what is written; these rules decide the master code and the numbers, so an unknown
item is never mapped to a similar registered one (SUS430 is not SUS304, a burring tap is not a tap, a
stand-off is not a press-fit stud). Each resolver returns a value or None when the text cannot be
interpreted with confidence - the caller then asks for a review.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from src.master_loader import UNREGISTERED, MasterLoader, normalize_term

THREAD = r"(?:(?<=\dX)|(?<=\dX\s)|(?<![A-Z]))M(\d{1,2})"  # M4, also in "2XM4" (2×M4)
SEPARATORS = r"[\s\-_・/()（）\[\]「」,.、。:：]*"
AMBIGUOUS = "AMBIGUOUS"  # more than one reading fits: never guess


def upper(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "").upper()
    return text.replace("×", "X").replace("Ø", "Φ").replace("⌀", "Φ").replace("ø", "Φ")


def _term_pattern(term: str) -> re.Pattern:
    chars = normalize_term(term)
    body = SEPARATORS.join(re.escape(c) for c in chars)
    before = r"(?<![A-Z])" if chars[:1].isalpha() and chars[:1].isascii() else ""
    after = r"(?![0-9])" if chars[-1:].isdigit() else ""
    return re.compile(before + body + after)


# ================================================================== material
GENERIC_MATERIAL = re.compile(
    r"(?<![A-Z])(SUS\s?-?\d{3}[A-Z]{0,2}|SG[CH]C|SPTE|SPFC\s?\d*|SAPH\s?\d*|STKM\s?\d*|SUH\s?\d*|SK\d{1,2}|S\d{2}C"
    r"|C\s?\d{4}\s?P?|A\s?\d{4}\s?P?|AL\s?\d{4}|SS\s?\d{3}|SP[CH][CDE]\w*|SECC\w*)")


class Terms:
    def __init__(self, masters: MasterLoader | None = None) -> None:
        self.masters = masters or MasterLoader("data")
        self._alias = {}
        for kind, rows in (("material", self.masters.materials), ("surface_treatment", self.masters.surface_treatments)):
            terms = []
            for code, row in rows.items():
                for term in {code, row.get("display_name", ""), *self.masters.aliases_of(row)}:
                    if normalize_term(term):
                        terms.append((len(normalize_term(term)), _term_pattern(term), code))
            self._alias[kind] = sorted(terms, key=lambda x: -x[0])

    # ------------------------------------------------------------ material
    def material(self, text: str | None) -> str | None:
        """Master code, UNREGISTERED, AMBIGUOUS, or None (no material written / not interpretable)."""
        text = strip_label(text)
        if not text or not text.strip():
            return None
        exact = self.masters.resolve_alias("material", text)
        if exact:
            return exact
        up = upper(text)
        registered, spans = set(), []
        for _, pattern, code in self._alias["material"]:
            for m in pattern.finditer(up):
                if any(m.start() < e and s < m.end() for s, e in spans):
                    continue
                registered.add(code)
                spans.append((m.start(), m.end()))
        unregistered = [m.group(0) for m in GENERIC_MATERIAL.finditer(up)
                        if not any(m.start() < e and s < m.end() for s, e in spans)]
        if len(registered) == 1 and not unregistered:
            return registered.pop()
        if unregistered and not registered:
            return UNREGISTERED
        if registered or unregistered:
            return AMBIGUOUS
        return None

    # ------------------------------------------------------------ surface treatment
    FINISH_UNREGISTERED = re.compile(
        r"クロムめっき|クロムメッキ|硬質クロム|CHROME\s*PLAT|HARD\s*CHROME|パーカー|リン酸|燐酸|PHOSPHAT|カチオン|電着|E-?COAT"
        r"|ED塗装|錫|TIN\s*PLAT|化成処理|ALODINE|CHEM\S*\s*FILM|CHROMATE\s*CONVERSION(?!.*ZINC)|硬質アルマイト|HARD\s*ANOD"
        r"|六価|HEXAVALENT|銅めっき|COPPER\s*PLAT|金めっき|GOLD\s*PLAT|銀めっき|SILVER\s*PLAT|溶融亜鉛|HOT[-\s]*DIP|ドブ|DACROT|ダクロ")
    FINISH_FAMILIES = [
        ("ELECTROLESS_NI", re.compile(r"無電解|ELECTROLESS|カニゼン|KANIGEN")),
        ("ANODIZE", re.compile(r"アルマイト|ANODI[ZS]|陽極酸化")),
        ("ZINC", re.compile(r"亜鉛|ZINC|(?<![A-Z])ZN(?![A-Z])|ユニクロ|クロメート|CHROMATE|三価|TRIVALENT")),
        ("POWDER_COAT", re.compile(r"粉体|紛体|POWDER")),
        ("BAKING_PAINT", re.compile(r"焼付|焼き付|BAKED|BAKING|メラミン")),
    ]
    NONE_WORDS = re.compile(r"^(なし|無し|ナシ|生地|生地のまま|無処理|処理なし|NONE|NO\s*FINISH|UNFINISHED|NO\s*TREATMENT|AS\s*IS|-|―|ー)$")

    def finish(self, text: str | None) -> str | None:
        text = strip_label(text)
        if not text or not text.strip():
            return None
        exact = self.masters.resolve_alias("surface_treatment", text)
        up = upper(text).strip()
        if self.material(text) not in (None, UNREGISTERED, AMBIGUOUS) and not re.search(r"めっき処理|塗装|アルマイト|処理", up):
            return None  # a material name ("電気亜鉛めっき鋼板 SECC") is not a surface treatment
        if self.FINISH_UNREGISTERED.search(up):
            return UNREGISTERED
        families = [name for name, pattern in self.FINISH_FAMILIES if pattern.search(up)]
        if "ZINC" in families and "ELECTROLESS_NI" in families:
            families.remove("ZINC")
        if len(families) > 1:
            return exact or AMBIGUOUS
        if families:
            family = families[0]
            black = re.search(r"黒|BLACK|ブラック", up)
            if family == "ZINC":
                yellow = re.search(r"有色|YELLOW|イエロー|黄", up)
                if black and yellow:
                    return AMBIGUOUS
                code = "ZINC_BLACK" if black else "ZINC_YELLOW" if yellow else "ZINC_CLEAR"
            elif family == "ANODIZE":
                other_colour = re.search(r"赤|青|金|RED|BLUE|GOLD|GREEN|緑", up)
                if other_colour:
                    return UNREGISTERED
                code = "ANODIZE_BLACK" if black else "ANODIZE_CLEAR"
            elif family == "ELECTROLESS_NI":
                code = "ELECTROLESS_NI"
            else:
                code = family
            if exact and exact != code:
                return AMBIGUOUS
            return code
        if self.NONE_WORDS.match(up) or exact == "NONE" or re.fullmatch(
                r"(?:表面)?(?:処理)?(?:は)?\s*(なし|無し|ナシ|生地|生地のまま|無処理|NONE|NO\s*FINISH|UNFINISHED)\s*(?:のこと|とする|で)?", up):
            return "NONE"
        if exact:
            return exact
        if re.search(r"めっき|メッキ|PLAT|塗装|PAINT|COAT|処理|FINISH", up):
            return UNREGISTERED  # a treatment is written, but none of the master families
        return None

    # ------------------------------------------------------------ numbers
    @staticmethod
    def thickness(text: str | None) -> float | None:
        if text is None:
            return None
        up = upper(str(text))
        found = {float(v) for v in re.findall(r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)(?![\d.])", up)}
        found = {v for v in found if 0.1 <= v <= 25}
        return found.pop() if len(found) == 1 else None

    @staticmethod
    def quantity(text: str | None) -> int | None:
        if text is None:
            return None
        up = upper(str(text)).replace(",", "")
        found = {int(v) for v in re.findall(r"(?<![\d.])(\d{1,6})(?![\d.])", up)}
        return found.pop() if len(found) == 1 else None

    # ------------------------------------------------------------ processes
    TAP_SIZES = {3, 4, 5, 6, 8}
    COUNT_UNITS = r"(?:ヶ所|箇所|カ所|か所|ケ所|個所|ヵ所|個|点|本|PLCS|PLACES|PLACE|PL|CORNERS|PCS|EA)"

    PLAIN_HOLE = re.compile(r"キリ|下穴|用\s*[)）]?$|FOR\s+M\d|THRU|抜き|貫通|^\s*\d*\s*[-X]?\s*Φ\s*[\d.]+\s*$|^\s*[□口]\s*[\d.]+")

    def is_plain_hole(self, text: str | None) -> bool:
        """A plain hole / cut-out (not an additional process): 2-φ10, φ4.5キリ（M4用）, Ø4.5 THRU (FOR M4), □40×20抜き."""
        up = upper(text)
        return bool(up.strip()) and bool(self.PLAIN_HOLE.search(up)) and self.process(text) == []

    DECORATION = re.compile(r"図示位置|[（(]\s*図示\s*[)）]|図示の?通り|AS\s+SHOWN|お願いします|のこと|とする|を?追加|^\s*\+\s*")

    def process(self, text: str | None) -> list[tuple[str, int | None]] | None:
        """[(code, count per callout)] for one callout / note / handwritten line; [] = not an additional
        process (plain hole, cut-out, ...); None = a process that cannot be interpreted."""
        if not text or not text.strip():
            return []
        up = self.DECORATION.sub(" ", upper(text)).strip()
        if re.search(r"バーリング|BURRING|BURR\s*TAP|スペーサ|SPACER|STANDOFF|STAND-OFF|(?<![A-Z])SO-M|リベット|RIVET|ヘリサート|HELI-?COIL|インサート|INSERT", up):
            return [(UNREGISTERED, self._count(up))]
        if re.search(r"ナット|(?<![A-Z])NUT(?![A-Z])|CLS-M|(?<![A-Z])S-M\d", up):
            return [("PRESS_NUT", self._count(up))] if re.search(r"圧入|PEM|クリンチ|CLINCH|CLS|(?<![A-Z])S-M", up) else [(UNREGISTERED, self._count(up))]
        if re.search(r"スタッド|STUD|FH-M|FHS-M", up):
            return [("PRESS_STUD", self._count(up))] if re.search(r"圧入|PEM|クリンチ|CLINCH|FH", up) else [(UNREGISTERED, self._count(up))]
        if re.search(r"スポット|(?<![A-Z])SPOT(?![A-Z])", up):
            return [("SPOT_WELD", self._count(up))]
        if re.search(r"(?<![A-Z])TIG(?![A-Z])", up):
            return [("TIG_WELD", self._count(up))]
        if re.search(r"溶接|WELD", up):
            return [(UNREGISTERED, self._count(up))]
        if re.search(r"皿もみ|皿穴|皿ザグリ|(?<![A-Z])皿(?!ビス)|CSK|COUNTERSINK|サラ", up):
            return [("COUNTERSINK", self._count(up))]
        size = re.search(THREAD + r"(?:\s*X\s*[\d.]+|\s*-\s*[\d.]+(?!\s*-))?", up)
        tapped = re.search(r"タップ|TAP|THD|THREAD|ネジ|ねじ", up)
        drilled = re.search(r"キリ|用|FOR\s+M|下穴|THRU\s*\(|CLEARANCE", up) and not tapped
        if size and not drilled and (tapped or re.search(r"(?<![\d.])\d+\s*[-X]\s*M\d", up)):
            d = int(size.group(1))
            code = f"TAP_M{d}" if d in self.TAP_SIZES else UNREGISTERED
            return [(code, self._count(up))]
        return []

    def _count(self, up: str) -> int | None:
        # thread size / pitch / length ("M4X0.7", "M4 X 0.7", "M4X10", "M4-0.7"); "M4 X2" keeps its count
        s = re.sub(THREAD + r"(?:X[\d.]+|\s*X\s*\d+\.\d+|\s*-\s*[\d.]+)?", " ", up)
        s = re.sub(r"(?:CLS|FH|FHS|SO|S)-M?\d+(?:-\d+)*", " ", s)                           # hardware part numbers
        s = re.sub(r"Φ\s*[\d.]+|R\s*[\d.]+|[\d.]+\s*°|[\d.]+\s*(?:UM|ΜM|MM)|X\s*90", " ", s)  # diameters, angles
        s = re.sub(r"\d+\s*/\s*\d+|\d{4}[./-]\d{1,2}[./-]\d{1,2}", " ", s)                  # dates
        counts = set()
        for pattern in (r"^\s*(\d+)\s*[-X]", r"(?<![\d.])(\d+)\s*[-X]\s*(?=[MΦ])", r"(\d+)\s*" + self.COUNT_UNITS, r"[X]\s*(\d+)\s*(?:" + self.COUNT_UNITS + r")?\s*$",
                        r"[（(]\s*(\d+)\s*(?:" + self.COUNT_UNITS + r")?\s*[)）]", r"(\d+)\s*X\s"):
            counts |= {int(v) for v in re.findall(pattern, s)}
        return counts.pop() if len(counts) == 1 else None

    # ------------------------------------------------------------ rush / flags
    RUSH_WORD = r"(?:特急|至急|大至急|急ぎ|短納期|URGENT|RUSH|EXPEDITE|ASAP)"
    RUSH_NEG = re.compile(
        r"急ぎません|急がない|通常|別途|STANDARD\s+LEAD|NON[\s-]?URGENT"
        r"|NO[TN]?[\s-]*(?:URGENT|RUSH|EXPEDITE)"                                   # NO URGENT, NOT URGENT, NO RUSH
        r"|" + RUSH_WORD + r"\s*(?:[:：=]\s*)?(?:は\s*)?(?:無|否|不可|X(?!\w)|NO\b|NONE|N/?A|NOT\s+REQUIRED|-+\s*$)"
        r"|" + RUSH_WORD + r"[^、。,.\n]{0,6}?(?:不要|なし|無し|しない|でなくて)"                 # 特急不要, 特急扱いなし
        r"|(?:不要|なし|無し)\s*[:：]?\s*" + RUSH_WORD)                                # 不要：特急
    RUSH_POS = re.compile(r"特急|至急|急ぎ|大至急|短納期|URGENT|RUSH|EXPEDITE|ASAP")

    def rush(self, text: str | None) -> bool | None:
        up = upper(text)
        if not up.strip():
            return None
        if self.RUSH_NEG.search(up):
            return False
        return True if self.RUSH_POS.search(up) else None

    GENERAL_TOLERANCE = re.compile(r"普通公差|普通寸法公差|JIS\s*B\s*0405|ISO\s*2768|UNLESS\s+OTHERWISE|GENERAL\s+TOL")
    COLOUR_ONLY = re.compile(r"マンセル|MUNSELL|RAL\s*\d|艶|GLOSS|色|COLOU?R|アイボリー|IVORY|N\d(?![\d.])")
    APPEARANCE = re.compile(r"外観|化粧|キズ|傷|打痕|COSMETIC|SCRATCH|DENT|CLASS\s*A")

    FLAG_WORDS = {
        "tolerance": re.compile(r"公差|平面度|真直度|平行度|直角度|位置度|FLATNESS|STRAIGHTNESS|PARALLELISM|PERPENDICULARITY|"
                                r"TOLERANCE|TRUE\s+POSITION|厳守|(?:ピッチ|PITCH|寸法|DIM\S*)[^±]{0,12}±\s*0?\.\d+"),
        "appearance": re.compile(r"外観|化粧面|COSMETIC|キズ.{0,4}(?:不可|なき)|打痕|SCRATCH|DENT"),
        "inspection": re.compile(r"検査成績|成績書|全数.{0,4}検査|初品検査|ミルシート|INSPECTION|CERTIFICATE|MILL\s*SHEET|検査.{0,6}(?:要|提出|のこと)"),
    }

    def flag_categories(self, text: str | None) -> set[str]:
        """Special-requirement categories a note text states (general tolerances and colours excluded)."""
        up = upper(text)
        return {cat for cat, pattern in self.FLAG_WORDS.items() if pattern.search(up) and self.flag_ok(cat, text)}

    def flag_ok(self, category: str, text: str | None) -> bool:
        """False for texts that look like a special requirement but are not (general tolerances,
        paint colour / gloss)."""
        up = upper(text)
        if category == "tolerance" and self.GENERAL_TOLERANCE.search(up):
            return False
        if category == "appearance" and self.COLOUR_ONLY.search(up) and not self.APPEARANCE.search(up):
            return False
        return True


LABEL = re.compile(r"^\s*(?:\d+[.)）]\s*)?(?:表面処理|表面仕上げ?|処理|材質|材料|板厚|厚さ|厚み|数量|個数|製作数(?:量)?|手配数|"
                   r"FINISH|SURFACE\s+TREATMENT|MATERIAL(?:\s+THICKNESS)?|THICKNESS|THK|QTY|QUANTITY|Q'TY)\s*[:：]?\s*", re.I)


def strip_label(text: str | None) -> str | None:
    """"表面処理：無処理" -> "無処理", "MATERIAL: SPCC" -> "SPCC"."""
    if text is None:
        return None
    return LABEL.sub("", unicodedata.normalize("NFKC", text), count=1)


@dataclass
class Divided:
    value: int | None
    exact: bool


def per_part(total: int, quantity: int | None) -> Divided:
    if not quantity:
        return Divided(None, False)
    return Divided(total // quantity, total % quantity == 0)
