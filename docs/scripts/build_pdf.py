"""Build the documents in docs/ as PDF (docs/pdf/). No LLM API.

    python docs/scripts/build_pdf.py            # all documents
    python docs/scripts/build_pdf.py 02         # only the ones whose file name starts with 02

What it does, for each docs/0N_*.md:
  1. Mermaid blocks (```mermaid 図のキャプション) are drawn to SVG with mermaid.js in Chromium and cached in
     docs/images/diagrams/ (so a rebuild without Node works as long as the diagrams did not change).
     To draw new or changed diagrams: `cd docs/scripts && npm install` once.
  2. Markdown -> HTML (python-markdown), headings numbered, figures with captions, the SVG inlined.
  3. Chromium (Playwright) prints A4 with page numbers; the page of every heading is read from the PDF outline
     and written into the table of contents (second print). A cover page is added in front.
Fonts: the IPA Gothic of the repository (fonts/ipag.ttf) is embedded, so the PDF reads the same on Windows.
Requirements: `pip install -r docs/requirements.txt` (markdown), Playwright with Chromium, pypdfium2.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

import markdown
import pypdfium2 as pdfium
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
OUT = DOCS / "pdf"
DIAGRAMS = DOCS / "images" / "diagrams"
FONT = ROOT / "fonts" / "ipag.ttf"
TMP = DOCS / ".build.html"  # pages are opened from a file so that images and the font load (git-ignored)
MERMAID_JS = DOCS / "scripts" / "node_modules" / "mermaid" / "dist" / "mermaid.min.js"
CHROMIUM_CANDIDATES = [Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")]
SYSTEM_NAME = "板金見積システム（STEP・DXF・図面PDFからの見積）"

CSS = """
@font-face { font-family: 'DocFont'; src: url('%(font)s'); }
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
html { font-family: 'DocFont', sans-serif; font-size: 9.6pt; color: #1d2733; line-height: 1.65; }
body { margin: 0; }
h1, h2, h3, h4 { color: #123a5a; line-height: 1.35; break-after: avoid; }
h1.doc-title { font-size: 19pt; margin: 0 0 4mm; border-bottom: 2px solid #123a5a; padding-bottom: 2mm; }
h2 { font-size: 14pt; margin: 9mm 0 3mm; padding: 1.5mm 3mm; background: #eaf1f7; border-left: 5px solid #1f6aa5; }
h2.chapter { margin-top: 10mm; }
h2.newpage { break-before: page; margin-top: 0; }
h3 { font-size: 11.5pt; margin: 6mm 0 2mm; border-bottom: 1px solid #c8d6e3; padding-bottom: 1mm; }
h4 { font-size: 10pt; margin: 4mm 0 1.5mm; }
p { margin: 1.5mm 0 2.5mm; }
ul, ol { margin: 1mm 0 2.5mm; padding-left: 6mm; }
li { margin: 0.6mm 0; }
code { font-family: 'DocFont', monospace; background: #f1f3f6; padding: 0 1mm; border-radius: 2px; font-size: 8.8pt; }
pre { background: #f5f7fa; border: 1px solid #d6dde5; border-radius: 3px; padding: 2.5mm 3mm; font-size: 8.2pt;
      line-height: 1.45; white-space: pre-wrap; word-break: break-all; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%%; margin: 2mm 0 4mm; font-size: 8.4pt; line-height: 1.45; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { background: #dde8f2; color: #123a5a; font-weight: bold; }
td:first-child, th:first-child { min-width: 13mm; }
th, td { border: 1px solid #aebfcf; padding: 1.2mm 1.8mm; vertical-align: top; word-break: break-word; }
td:has(> strong:only-child) { }
blockquote { margin: 3mm 0; padding: 2mm 4mm; background: #fff7e0; border-left: 4px solid #e0a100; color: #4a3b00; }
blockquote p { margin: 1mm 0; }
figure { margin: 3mm 0 5mm; text-align: center; break-inside: avoid; }
figure img { max-width: 100%%; max-height: 205mm; border: 1px solid #d0d8e0; }
figure img.noborder, figure.diagram img { border: none; }
figure.diagram svg { max-width: 100%%; height: auto; max-height: 215mm; }
figure.small img { max-width: 62%%; }
figure.medium img { max-width: 80%%; }
figcaption { font-size: 8.4pt; color: #4a5a6a; margin-top: 1.5mm; }
.note { background: #eef6ee; border-left: 4px solid #3c8c3c; padding: 2mm 4mm; margin: 3mm 0; }
.pagebreak { break-after: page; }
nav.toc { break-after: page; }
nav.toc h2 { break-before: auto; }
nav.toc ol { list-style: none; padding-left: 0; margin: 0; }
nav.toc li { display: flex; align-items: baseline; margin: 0.8mm 0; }
nav.toc li.l3 { padding-left: 7mm; font-size: 8.8pt; color: #334; }
nav.toc li.l2 { font-weight: bold; margin-top: 2mm; }
nav.toc li .t { white-space: nowrap; overflow: hidden; color: inherit; text-decoration: none; }
a { color: #1f6aa5; text-decoration: none; }
nav.toc li .dots { flex: 1; border-bottom: 1px dotted #9aa8b6; margin: 0 1.5mm; transform: translateY(-1mm); }
nav.toc li .p { min-width: 8mm; text-align: right; }
.cover { height: 250mm; display: flex; flex-direction: column; justify-content: center; }
.cover .system { font-size: 12pt; color: #1f6aa5; letter-spacing: 0.05em; }
.cover h1 { font-size: 30pt; color: #123a5a; margin: 6mm 0 10mm; border-bottom: 3px solid #123a5a; padding-bottom: 4mm; }
.cover table { width: 70%%; font-size: 10.5pt; }
.cover th { width: 28%%; text-align: left; }
.cover .foot { margin-top: 18mm; font-size: 9pt; color: #56697c; }
"""


# ---------------------------------------------------------------- Mermaid
MERMAID_BLOCK = re.compile(r"^```mermaid[ \t]*(.*?)\n(.*?)^```[ \t]*$", re.S | re.M)


def diagram_key(code: str) -> str:
    return hashlib.sha1(code.strip().encode("utf-8")).hexdigest()[:14]


def render_diagrams(codes: dict[str, str], browser) -> None:
    """Draw the Mermaid diagrams that are not cached yet (docs/images/diagrams/<key>.svg)."""
    missing = {k: c for k, c in codes.items() if not (DIAGRAMS / f"{k}.svg").exists()}
    if not missing:
        return
    if not MERMAID_JS.exists():
        raise SystemExit("図を描くには mermaid.js が必要です: cd docs/scripts && npm install")
    DIAGRAMS.mkdir(parents=True, exist_ok=True)
    page = browser.new_page()
    TMP.write_text(f"<html><head><meta charset='utf-8'><style>@font-face {{ font-family: 'DocFont'; src: url('{FONT.as_uri()}'); }}"
                   "body { font-family: 'DocFont'; }</style></head><body><div id='probe'>板金 あ</div></body></html>",
                   encoding="utf-8")
    page.goto(TMP.as_uri())
    page.add_script_tag(path=str(MERMAID_JS))
    page.evaluate("document.fonts.ready")
    page.evaluate("""() => mermaid.initialize({startOnLoad: false, securityLevel: 'loose', theme: 'base',
        fontFamily: 'DocFont', flowchart: {htmlLabels: false, curve: 'basis', useMaxWidth: false},
        sequence: {useMaxWidth: false, mirrorActors: false}, state: {useMaxWidth: false},
        themeVariables: {fontFamily: 'DocFont', fontSize: '14px', primaryColor: '#e3eef8', primaryBorderColor: '#1f6aa5',
          primaryTextColor: '#123a5a', lineColor: '#48607a', secondaryColor: '#fdf1d8', tertiaryColor: '#eef6ee',
          clusterBkg: '#f6f9fc', clusterBorder: '#9fb6cc', noteBkgColor: '#fff7e0', noteBorderColor: '#e0a100',
          actorBkg: '#e3eef8', actorBorder: '#1f6aa5', signalColor: '#34495e', edgeLabelBackground: '#ffffff'}})""")
    for key, code in missing.items():
        svg = page.evaluate("async ([id, code]) => (await mermaid.render(id, code)).svg", [f"d{key}", code])
        (DIAGRAMS / f"{key}.svg").write_text(svg, encoding="utf-8")
    page.close()


# ---------------------------------------------------------------- Markdown -> HTML
def read_meta(text: str) -> tuple[str, dict, str]:
    title_match = re.search(r"^# (.+)$", text, re.M)
    title = title_match.group(1).strip()
    meta = {}
    meta_match = re.search(r"<!--\s*meta\s*(\{.*?\})\s*-->", text, re.S)
    if meta_match:
        meta = json.loads(meta_match.group(1))
        text = text.replace(meta_match.group(0), "")
    body = text[title_match.end():]
    return title, meta, body


def to_html(body: str) -> tuple[str, dict[str, str]]:
    codes: dict[str, str] = {}
    captions: dict[str, str] = {}

    def keep(m):
        caption, code = m.group(1).strip(), m.group(2)
        key = diagram_key(code)
        codes[key] = code
        captions[key] = caption
        return f"\n\nDIAGRAM@{key}@\n\n"

    body = MERMAID_BLOCK.sub(keep, body)
    out = markdown.markdown(body, extensions=["tables", "fenced_code", "attr_list", "sane_lists", "md_in_html"])
    for key in codes:
        caption = captions[key]
        out = out.replace(f"<p>DIAGRAM@{key}@</p>",
                          f'<figure class="diagram">SVG@{key}@' + (f"<figcaption>{html.escape(caption)}</figcaption>" if caption else "")
                          + "</figure>")
    # images alone in a paragraph -> figure with the alt text as caption; {.small} etc. via attr_list on the img
    def figure(m):
        img = m.group(1)
        alt = re.search(r'alt="([^"]*)"', img)
        cls = re.search(r'class="([^"]*)"', img)
        caption = alt.group(1) if alt else ""
        return (f'<figure class="{cls.group(1) if cls else ""}">{img}'
                + (f"<figcaption>{caption}</figcaption>" if caption else "") + "</figure>")

    out = re.sub(r"<p>(<img [^>]+>)</p>", figure, out)
    return out, codes


def number_headings(body_html: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Number h2 / h3 (1. / 1.1) and return the entries of the table of contents."""
    counters = [0, 0]
    entries = []

    def repl(m):
        level, attrs, text = int(m.group(1)), m.group(2), m.group(3)
        if level == 2:
            counters[0] += 1
            counters[1] = 0
            number = f"{counters[0]}."
        else:
            counters[1] += 1
            number = f"{counters[0]}.{counters[1]}"
        label = f"{number} {re.sub('<[^>]+>', '', text)}"
        anchor = f"h-{counters[0]}-{counters[1]}"
        entries.append((level, label, anchor))
        cls = ""
        if level == 2 and counters[0] > 1:
            cls = ' class="newpage"' if "newpage" in attrs else ' class="chapter"'
        return f'<h{level} id="{anchor}"{cls}>{number} {text}</h{level}>'

    return re.sub(r"<h([23])([^>]*)>(.*?)</h\1>", repl, body_html), entries


def toc_html(entries, pages: dict[str, int] | None) -> str:
    items = []
    for level, label, anchor in entries:
        page = pages.get(label, "") if pages else "00"
        items.append(f'<li class="l{level}"><a class="t" href="#{anchor}">{html.escape(label)}</a>'
                     f'<span class="dots"></span><span class="p">{page}</span></li>')
    return f'<nav class="toc"><h2>目次</h2><ol>{"".join(items)}</ol></nav>'


def page_html(css: str, content: str) -> str:
    return (f'<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><base href="{DOCS.as_uri()}/">'
            f"<style>{css}</style></head><body>{content}</body></html>")


def cover_html(title: str, meta: dict) -> str:
    rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in (
        ("版", meta.get("version", "1.0")), ("日付", meta.get("date", date.today().isoformat())),
        ("対象読者", meta.get("audience", "")), ("文書番号", meta.get("id", ""))) if v)
    return (f'<div class="cover"><div class="system">{SYSTEM_NAME}</div><h1>{html.escape(title)}</h1>'
            f"<table>{rows}</table>"
            f'<div class="foot">{html.escape(meta.get("note", "本資料の顧客名・図番・金額はすべて架空のデータです。"))}</div></div>')


# ---------------------------------------------------------------- printing
def print_pdf(page, content: str, title: str, footer: bool) -> bytes:
    TMP.write_text(content, encoding="utf-8")
    page.goto(TMP.as_uri(), wait_until="load")
    page.evaluate("document.fonts.ready")
    options = dict(format="A4", print_background=True, outline=True, tagged=True,
                   margin={"top": "16mm", "bottom": "17mm", "left": "15mm", "right": "15mm"})
    if footer:
        options.update(display_header_footer=True, header_template="<span></span>",
                       footer_template=('<div style="font-family:IPAGothic,sans-serif;font-size:7.5px;color:#6b7c8d;width:100%;'
                                        'padding:0 15mm;display:flex;justify-content:space-between;">'
                                        f"<span>{html.escape(title)}</span>"
                                        '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>'))
    return page.pdf(**options)


def heading_pages(pdf: bytes) -> dict[str, int]:
    doc = pdfium.PdfDocument(pdf)
    pages = {}
    for item in doc.get_toc():
        title = item.get_title().strip()
        index = item.get_dest().get_index() if item.get_dest() else None
        if index is not None and title not in pages:
            pages[title] = index + 1
    return pages


def build(md_path: Path, browser) -> Path:
    text = md_path.read_text(encoding="utf-8")
    title, meta, body = read_meta(text)
    body_html, codes = to_html(body)
    render_diagrams(codes, browser)
    for key in codes:
        svg = (DIAGRAMS / f"{key}.svg").read_text(encoding="utf-8")
        body_html = body_html.replace(f"SVG@{key}@", svg)
    body_html, entries = number_headings(body_html)
    css = CSS % {"font": FONT.as_uri()}
    head = ""  # the title is on the cover
    page = browser.new_page()
    first = print_pdf(page, page_html(css, toc_html(entries, None) + head + body_html), title, footer=True)
    pages = heading_pages(first)
    missing = [label for _, label, _ in entries if label not in pages]
    if missing:
        print(f"  目次のページ番号が見つからない見出し: {missing[:5]}", file=sys.stderr)
    body_pdf = print_pdf(page, page_html(css, toc_html(entries, pages) + head + body_html), title, footer=True)
    if heading_pages(body_pdf) != pages:  # the numbers changed the layout: once more
        pages = heading_pages(body_pdf)
        body_pdf = print_pdf(page, page_html(css, toc_html(entries, pages) + head + body_html), title, footer=True)
    cover_pdf = print_pdf(page, page_html(css, cover_html(title, meta)), title, footer=False)
    page.close()
    TMP.unlink(missing_ok=True)
    document = pdfium.PdfDocument(cover_pdf)
    document.import_pages(pdfium.PdfDocument(body_pdf))
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / (md_path.stem + ".pdf")
    document.save(str(out))
    print(f"{out.relative_to(ROOT)}: {len(document)} ページ（表紙1＋本文{len(document) - 1}）、図 {len(codes)}")
    return out


def main(argv: list[str]) -> int:
    targets = sorted(p for p in DOCS.glob("0[0-9]_*.md") if not argv or any(p.name.startswith(a) for a in argv))
    executable = next((str(p) for p in CHROMIUM_CANDIDATES if p.exists()), None)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=executable) if executable else pw.chromium.launch()
        for path in targets:
            build(path, browser)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
