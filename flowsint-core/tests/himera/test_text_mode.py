"""Text input mode: local text with page markers instead of a PDF attachment."""

from dataclasses import replace

import pytest

from flowsint_core.himera.config import HimeraSettings
from flowsint_core.himera.extraction import HimeraExtractionService
from flowsint_core.himera.pdf import PageRange, page_texts, text_with_markers

from .fakes import (
    FIXTURES,
    FakeOpenRouter,
    perfect_entities_handler,
    perfect_records_handler,
    synthetic_pdf,
)
from .test_records_pass import EXPECTED, summary

TEXT = HimeraSettings(pages_per_chunk=0, max_cost_usd=1.0)  # input_mode="auto"


def test_page_texts_keep_label_value_columns():
    pages = page_texts(synthetic_pdf())
    assert len(pages) == 4
    first = pages[0].splitlines()
    phones = next(line for line in first if line.startswith("Телефоны"))
    # label and value stay on one line, separated by spaces
    assert "79990001122" in phones
    assert "   " in phones and "    " not in phones  # long runs shortened
    marked = text_with_markers(pages, PageRange(2, 3))
    assert marked.startswith("=== PAGE 2 ===\n")
    assert "\n\n=== PAGE 3 ===\n" in marked
    assert "=== PAGE 1 ===" not in marked


async def run(settings, pdf=None):
    fake = FakeOpenRouter(
        {
            "himera_records": perfect_records_handler(),
            "himera_entities": perfect_entities_handler(),
        }
    )
    result = await HimeraExtractionService(fake, settings).extract(
        pdf or synthetic_pdf()
    )
    return result, fake


@pytest.mark.asyncio
async def test_text_mode_sends_marked_text_without_pdf():
    result, fake = await run(TEXT)
    call = fake.calls_for("himera_records")[0]
    content = call["messages"][1]["content"]
    assert isinstance(content, str)  # no file part
    assert "=== PAGE 1 ===" in content and "=== PAGE 4 ===" in content
    assert call["pdf_engine"] is None  # no OpenRouter file parser
    assert result.usage.pdf_engine == "text"
    assert summary(result) == EXPECTED


@pytest.mark.parametrize("size,overlap", [(2, 1), (1, 0), (3, 1)])
@pytest.mark.asyncio
async def test_text_mode_report_pages_survive_chunking(size, overlap):
    result, fake = await run(
        replace(TEXT, pages_per_chunk=size, chunk_overlap_pages=overlap)
    )
    assert len(fake.calls_for("himera_records")) > 1
    assert summary(result) == EXPECTED
    assert not any("clamped" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_scanned_pdf_falls_back_to_pdf_ocr():
    pdf = (FIXTURES / "scanned_report.pdf").read_bytes()
    fake = FakeOpenRouter(
        {
            "himera_records": lambda s, m: {
                "report_id": None,
                "truncation_notice": False,
                "records": [],
            }
        }
    )
    result = await HimeraExtractionService(
        fake, replace(TEXT, input_mode="text")
    ).extract(pdf)
    call = fake.calls_for("himera_records")[0]
    assert call["pdf_engine"] == "mistral-ocr"
    assert isinstance(call["messages"][1]["content"], list)
    assert any("no text layer" in w for w in result.warnings)


def test_input_mode_from_env():
    assert HimeraSettings.from_env({}).input_mode == "auto"
    assert HimeraSettings.from_env({"HIMERA_INPUT": "pdf"}).input_mode == "pdf"
    with pytest.raises(ValueError):
        HimeraSettings.from_env({"HIMERA_INPUT": "html"})
