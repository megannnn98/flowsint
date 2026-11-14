from pathlib import Path

import pytest

from flowsint_core.himera.config import HimeraSettings
from flowsint_core.himera.pdf import (
    PageRange,
    PdfPreparationError,
    extract_pages,
    plan_page_ranges,
    prepare_pdf,
    split_range,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_text_pdf_detected_with_page_count():
    prepared = prepare_pdf((FIXTURES / "synthetic_report.pdf").read_bytes())
    assert prepared.page_count == 4
    assert prepared.has_text_layer
    assert len(prepared.sha256) == 64


def test_scanned_pdf_has_no_text_layer():
    prepared = prepare_pdf((FIXTURES / "scanned_report.pdf").read_bytes())
    assert prepared.page_count == 2
    assert not prepared.has_text_layer


def test_non_pdf_rejected():
    with pytest.raises(PdfPreparationError):
        prepare_pdf(b"hello")


@pytest.mark.parametrize(
    "count,size,overlap,expected",
    [
        (4, 0, 1, ["1-4"]),
        (4, 10, 1, ["1-4"]),
        (23, 6, 1, ["1-6", "6-11", "11-16", "16-21", "21-23"]),
        (10, 5, 0, ["1-5", "6-10"]),
        (5, 2, 5, ["1-2", "2-3", "3-4", "4-5"]),
    ],
)
def test_page_ranges_overlap(count, size, overlap, expected):
    assert [str(r) for r in plan_page_ranges(count, size, overlap)] == expected


def test_extract_pages_produces_subset_pdf():
    data = (FIXTURES / "synthetic_report.pdf").read_bytes()
    sub = extract_pages(data, PageRange(2, 3))
    assert prepare_pdf(sub).page_count == 2


def test_split_range():
    assert split_range(PageRange(3, 8)) == (PageRange(3, 5), PageRange(6, 8))
    with pytest.raises(ValueError):
        split_range(PageRange(2, 2))


def test_settings_from_env():
    s = HimeraSettings.from_env(
        {
            "HIMERA_MODEL": "x/y",
            "HIMERA_PDF_ENGINE": "mistral-ocr",
            "HIMERA_MAX_COST_USD": "0.1",
        }
    )
    assert (s.model, s.pdf_engine, s.max_cost_usd) == ("x/y", "mistral-ocr", 0.1)
    with pytest.raises(ValueError):
        HimeraSettings.from_env({"HIMERA_PDF_ENGINE": "magic"})
