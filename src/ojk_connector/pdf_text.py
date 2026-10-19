"""Text from an official PDF: its own text layer, page by page (pymupdf).

Pages are joined with the contract's ``<!-- page N -->`` markers, so a citation can point to a page of
the official file. A page with almost no text is a scan (or only a stamp or page number): it is counted,
and the text is reported incomplete, so it is listed for review instead of passing as whole.
Ported from the BPK connector's text-layer extractor; OCR is not wired in yet.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf
from metamorph_contract import join_pages

pymupdf.TOOLS.mupdf_display_errors(False)  # MuPDF prints parser warnings to stdout, corrupting CLI JSON output

MIN_PAGE_CHARS = 50  # fewer: a scanned page, or only a page number or stamp
HEADER_WINDOW = 1024  # "%PDF" may sit anywhere in the first 1024 bytes


class NotAPdf(ValueError):
    pass


@dataclass(frozen=True)
class PdfText:
    text: str  # pages joined with page markers
    pages: int
    pages_without_text: int

    @property
    def complete(self) -> bool:
        return self.pages > 0 and self.pages_without_text == 0


def is_pdf(data: bytes) -> bool:
    return b"%PDF" in data[:HEADER_WINDOW]


def extract_text(data: bytes) -> PdfText:
    if not is_pdf(data):
        raise NotAPdf("not a PDF")
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as e:
        raise NotAPdf(f"unreadable PDF: {e}") from e
    with document:
        pages = [page.get_text().strip() for page in document]
    return PdfText(join_pages(pages), len(pages), sum(len(page) < MIN_PAGE_CHARS for page in pages))
