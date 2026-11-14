"""
Technical PDF preparation: page count, text-layer detection, page-range split.

No semantic parsing happens here — the text is only measured (character count),
never interpreted. Semantic extraction is done by the LLM.
"""

import hashlib
import io
import re
from dataclasses import dataclass
from typing import List, Tuple

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

# A page with fewer extractable characters is treated as scanned (no text layer).
_MIN_TEXT_CHARS_PER_PAGE = 40
# Rough prompt-size estimate for the budget check, tokens per page of a dense
# Russian report (Cyrillic tokenizes at ~2-3 chars per token).
TOKENS_PER_PAGE_ESTIMATE = 1500


class PdfPreparationError(ValueError):
    pass


@dataclass(frozen=True)
class PageRange:
    first: int  # 1-based, inclusive
    last: int  # 1-based, inclusive

    @property
    def pages(self) -> int:
        return self.last - self.first + 1

    def __str__(self) -> str:
        return f"{self.first}-{self.last}"


@dataclass(frozen=True)
class PreparedPdf:
    data: bytes
    sha256: str
    page_count: int
    has_text_layer: bool
    text_chars: int


def prepare_pdf(data: bytes) -> PreparedPdf:
    if not data.startswith(b"%PDF"):
        raise PdfPreparationError("File is not a PDF")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PdfPreparationError("Encrypted PDFs are not supported")
        pages = reader.pages
        page_count = len(pages)
        text_pages = 0
        text_chars = 0
        for page in pages:
            chars = len((page.extract_text() or "").strip())
            text_chars += chars
            if chars >= _MIN_TEXT_CHARS_PER_PAGE:
                text_pages += 1
    except PdfReadError as e:
        raise PdfPreparationError(f"Cannot read PDF: {e}") from e
    if page_count == 0:
        raise PdfPreparationError("PDF has no pages")
    return PreparedPdf(
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        page_count=page_count,
        # Majority of pages carry text → treat as a text PDF.
        has_text_layer=text_pages * 2 >= page_count,
        text_chars=text_chars,
    )


def plan_page_ranges(
    page_count: int, pages_per_chunk: int, overlap: int
) -> List[PageRange]:
    """Split [1..page_count] into ranges; neighbours share ``overlap`` pages."""
    if pages_per_chunk <= 0 or pages_per_chunk >= page_count:
        return [PageRange(1, page_count)]
    overlap = max(0, min(overlap, pages_per_chunk - 1))
    step = pages_per_chunk - overlap
    ranges: List[PageRange] = []
    first = 1
    while True:
        last = min(first + pages_per_chunk - 1, page_count)
        ranges.append(PageRange(first, last))
        if last >= page_count:
            break
        first += step
    return ranges


def extract_pages(data: bytes, page_range: PageRange) -> bytes:
    """Return a new PDF containing only the pages of ``page_range``."""
    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter()
    for index in range(page_range.first - 1, page_range.last):
        writer.add_page(reader.pages[index])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def split_range(page_range: PageRange) -> Tuple[PageRange, PageRange]:
    """Halve a range (used when a model response was truncated)."""
    if page_range.pages < 2:
        raise ValueError("Cannot split a single-page range")
    middle = page_range.first + page_range.pages // 2 - 1
    return PageRange(page_range.first, middle), PageRange(middle + 1, page_range.last)


def page_texts(data: bytes) -> List[str]:
    """Text of every page with the column layout kept ("field   value" stays on
    one line), runs of spaces shortened to save tokens. Technical extraction
    only: the text is not interpreted here."""
    reader = PdfReader(io.BytesIO(data))
    out = []
    for page in reader.pages:
        text = page.extract_text(extraction_mode="layout") or ""
        lines = [re.sub(r" {3,}", "   ", line.strip()) for line in text.splitlines()]
        out.append("\n".join(line for line in lines if line))
    return out


def text_with_markers(pages: List[str], page_range: PageRange) -> str:
    return "\n\n".join(
        f"=== PAGE {n} ===\n{pages[n - 1]}"
        for n in range(page_range.first, page_range.last + 1)
    )
