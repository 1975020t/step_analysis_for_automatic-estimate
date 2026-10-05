"""pdfium (pypdfium2) is not thread-safe: two threads opening PDFs at once can corrupt memory and kill the
process. Every use of pdfium in the API process (the drawing reader's page loading, the previews, the text
extraction) runs under this one lock. The drawing reader's own code (src/pdf_reader.py) is unchanged: its
`load_pages` is wrapped once, so only the page loading is serialised, not the LLM calls."""
from __future__ import annotations

import functools
import threading

PDFIUM_LOCK = threading.RLock()


def guard_reader() -> None:
    import src.pdf_reader as reader

    if getattr(reader.load_pages, "_locked", False):
        return
    original = reader.load_pages

    @functools.wraps(original)
    def load_pages(path):
        with PDFIUM_LOCK:
            return original(path)

    load_pages._locked = True  # type: ignore[attr-defined]
    reader.load_pages = load_pages
