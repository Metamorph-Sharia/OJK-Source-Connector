"""Text from an official PDF: its own text layer, page by page (pymupdf).

Pages are joined with the contract's ``<!-- page N -->`` markers, so a citation can point to a page of
the official file. A page with almost no text is a scan (or only a stamp or page number): it is counted,
and the text is reported incomplete, so it is listed for review instead of passing as whole.
Ported from the BPK connector's text-layer extractor; OCR is not wired in yet.

A page's text is a printed line to a line (``metamorph_contract.printed_text``): pymupdf's own lines split a
justified line set a word at a time, and a list's number from its text. ``printed_lines=False`` keeps pymupdf's
own lines, as before (``METAMORPH_PDF_LINES=extracted``, read by the JDIH source).

With ``METAMORPH_PDF_ENGINE=mineru`` the JDIH source reads with ``extract_text_with`` instead: MinerU, its page
furniture removed and each page checked against the text layer (metamorph-pdf-text), and how it read the file kept
with the text.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf
from metamorph_contract import Extraction, ExtractionMethod, join_pages, printed_text
from metamorph_pdf_text import ExtractionFailed, ExtractPdf, text_extraction, text_method

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
    method: ExtractionMethod = ExtractionMethod.TEXT_LAYER
    extraction: Extraction | None = None  # how MinerU read it; None: read by pymupdf here

    @property
    def complete(self) -> bool:
        return self.pages > 0 and self.pages_without_text == 0


def is_pdf(data: bytes) -> bool:
    return b"%PDF" in data[:HEADER_WINDOW]


def extract_text(data: bytes, *, printed_lines: bool = True) -> PdfText:
    if not is_pdf(data):
        raise NotAPdf("not a PDF")
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as e:
        raise NotAPdf(f"unreadable PDF: {e}") from e
    with document:
        pages = [_page_text(page, printed_lines).strip() for page in document]
    return PdfText(join_pages(pages), len(pages), sum(len(page) < MIN_PAGE_CHARS for page in pages))


def extract_text_with(data: bytes, extract: ExtractPdf) -> PdfText:
    """The regulation's text as MinerU read it. A MinerU out of reach raises ExtractionUnavailable: no fallback."""
    if not is_pdf(data):
        raise NotAPdf("not a PDF")
    try:
        read = extract.run(data, "main")
    except ExtractionFailed as e:
        raise NotAPdf(f"unreadable PDF: {e}") from e
    pages = list(read.pages)
    without_text = sum(len(page.strip()) < MIN_PAGE_CHARS for page in pages)
    return PdfText(join_pages(pages), len(pages), without_text, text_method(read), text_extraction(read))


def _page_text(page: pymupdf.Page, printed_lines: bool) -> str:
    text = page.get_text()
    if not printed_lines:
        return text
    return printed_text(text, page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT))
