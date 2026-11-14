import json
from dataclasses import replace

import httpx
import pytest

from flowsint_core.himera.config import HimeraSettings
from flowsint_core.himera.extraction import HimeraExtractionService
from flowsint_core.himera.openrouter import (
    ChatResult,
    ModelCapabilityError,
    OpenRouterClient,
    Usage,
)

from .fakes import (
    FIXTURES,
    MODEL,
    FakeOpenRouter,
    load_json,
    perfect_records_handler,
    synthetic_pdf,
)

WHOLE = HimeraSettings(pages_per_chunk=0, max_cost_usd=1.0)


def summary(result):
    return [
        (r.record_id, r.source_heading, r.page_start, r.page_end)
        for r in result.records
    ]


EXPECTED = [
    ("p01-01", "УСТАНОВОЧНЫЕ ДАННЫЕ", 1, 1),
    ("p01-02", "Результаты скоринга", 1, 1),
    ("p01-03", "Метки", 1, 1),
    ("p01-04", "Доходы", 1, 1),
    ("p01-05", "Адреса", 1, 1),
    ("p02-01", "КЛИЕНТЫ БАНКА 2023 QV", 2, 2),
    ("p02-02", "КЛИЕНТЫ БАНКА 2023 QV", 2, 2),
    ("p02-03", "КАДРЫ ФЛ 2021 10 LU", 2, 3),
    ("p03-01", "ПОЛЬЗОВАТЕЛИ СОЦСЕТИ 2020 VK", 3, 3),
    ("p04-01", "Жители Москвы 2021 (совпадения по фио)", 4, 4),
    ("p04-02", "Служебная информация", 4, 4),
]


async def run(handlers, settings=WHOLE, pdf=None, model=MODEL):
    fake = FakeOpenRouter(handlers, model=model)
    service = HimeraExtractionService(fake, settings)
    return await service.extract(pdf or synthetic_pdf()), fake


@pytest.mark.asyncio
async def test_whole_document_records():
    result, fake = await run({"himera_records": perfect_records_handler()})
    assert summary(result) == EXPECTED
    assert result.report_id == "00000000-0000-4000-8000-000000000001"
    assert result.complete
    assert result.truncation_notice
    assert any("truncated" in w for w in result.warnings)
    assert [r.name_match_only for r in result.records].count(True) == 1
    assert fake.calls_for("himera_records")[0]["pdf_engine"] == "pdf-text"
    assert result.usage.calls == 2 and result.usage.model == "test/model"


@pytest.mark.asyncio
async def test_duplicate_records_of_one_source_are_kept_separate():
    result, _ = await run({"himera_records": perfect_records_handler()})
    bank = [r for r in result.records if r.source_heading.startswith("КЛИЕНТЫ БАНКА")]
    assert [r.record_id for r in bank] == ["p02-01", "p02-02"]
    assert bank[0].fields == bank[1].fields


@pytest.mark.parametrize("size,overlap", [(2, 1), (2, 0), (3, 1), (1, 0)])
@pytest.mark.asyncio
async def test_chunked_ranges_merge_to_same_records(size, overlap):
    settings = replace(WHOLE, pages_per_chunk=size, chunk_overlap_pages=overlap)
    result, fake = await run({"himera_records": perfect_records_handler()}, settings)
    assert len(fake.calls_for("himera_records")) > 1
    assert summary(result) == EXPECTED
    multi = next(r for r in result.records if r.record_id == "p02-03")
    names = [f.name for f in multi.fields]
    assert len(names) == len(set(names)) == 8  # continuation merged, no duplicates
    assert not multi.open_ended
    assert result.complete


@pytest.mark.asyncio
async def test_truncated_response_splits_range_and_recovers():
    perfect = perfect_records_handler()

    def handler(schema_name, messages):
        text = messages[1]["content"][0]["text"]
        if "pages 1-4" in text:
            return ChatResult('{"records": [', "length", Usage(10, 16000, 0.01, 1), "m")
        return perfect(schema_name, messages)

    result, fake = await run({"himera_records": handler})
    assert summary(result) == EXPECTED
    assert len(fake.calls_for("himera_records")) == 3
    assert any("token limit" in w for w in result.warnings)
    assert result.complete


@pytest.mark.asyncio
async def test_truncation_that_cannot_be_split_marks_incomplete():
    def handler(schema_name, messages):
        return ChatResult('{"records": [', "length", Usage(10, 10, 0.0, 1), "m")

    result, _ = await run({"himera_records": handler})
    assert not result.complete
    assert any("truncated" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_broken_json_is_repaired_once():
    good = load_json("llm_records_whole.json")

    def broken(schema_name, messages):
        return json.dumps(good, ensure_ascii=False)[:-30]

    def repair(schema_name, messages):
        return good

    result, fake = await run({"himera_records": broken, "repair": repair})
    assert len(result.records) == 11
    assert any("repaired" in w for w in result.warnings)
    assert len(fake.calls_for("himera_records")) == 2
    assert (
        fake.calls_for("himera_records")[1].get("pdf_engine") is None
    )  # repair is text-only


@pytest.mark.asyncio
async def test_unrepairable_json_reports_without_personal_data():
    def broken(schema_name, messages):
        return '{"records": [{"source_heading": "ПЕТРОВ ПЁТР", "page_start": "x"}]}'

    result, fake = await run({"himera_records": broken})
    assert not result.complete
    assert result.records == []
    joined = " ".join(result.warnings)
    assert "invalid JSON after repair" in joined
    assert "ПЕТРОВ" not in joined
    assert len(fake.calls_for("himera_records")) == 2


@pytest.mark.asyncio
async def test_empty_result_is_incomplete():
    result, _ = await run(
        {
            "himera_records": lambda s, m: {
                "report_id": None,
                "truncation_notice": False,
                "records": [],
            }
        }
    )
    assert not result.complete
    assert "no records extracted" in result.warnings


@pytest.mark.asyncio
async def test_budget_stops_further_calls():
    settings = replace(
        WHOLE, pages_per_chunk=1, chunk_overlap_pages=0, max_cost_usd=0.0075
    )
    result, fake = await run({"himera_records": perfect_records_handler()}, settings)
    # each call costs 0.001 + estimate ~0.0068 → only the first fits
    assert len(fake.calls_for("himera_records")) == 1
    assert not result.complete
    assert any("budget exceeded" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_scanned_pdf_uses_ocr_engine():
    pdf = (FIXTURES / "scanned_report.pdf").read_bytes()
    result, fake = await run(
        {
            "himera_records": lambda s, m: {
                "report_id": None,
                "truncation_notice": False,
                "records": [],
            }
        },
        pdf=pdf,
    )
    assert fake.calls_for("himera_records")[0]["pdf_engine"] == "mistral-ocr"
    assert fake.calls_for("himera_records")[0]["extra_cost_estimate_usd"] > 0


@pytest.mark.asyncio
async def test_native_engine_requires_file_capable_model():
    text_only = replace(MODEL, input_modalities=["text"])
    with pytest.raises(ModelCapabilityError):
        await run({}, settings=replace(WHOLE, pdf_engine="native"), model=text_only)


@pytest.mark.asyncio
async def test_real_client_over_mock_transport():
    """Correct OpenRouter response through the real HTTP client (no network)."""
    whole = load_json("llm_records_whole.json")
    models = {
        "data": [
            {
                "id": "google/gemini-2.5-flash",
                "context_length": 1000000,
                "pricing": {"prompt": "0.0000003", "completion": "0.0000025"},
                "architecture": {"input_modalities": ["text", "image", "file"]},
                "supported_parameters": ["structured_outputs", "response_format"],
            }
        ]
    }
    sent = []

    def handler(request: httpx.Request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=models)
        body = json.loads(request.content)
        if body["response_format"]["json_schema"]["name"] == "himera_entities":
            content = {"entities": [], "relationships": []}
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": json.dumps(content)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.0},
                },
            )
        sent.append(body)
        return httpx.Response(
            200,
            json={
                "model": "google/gemini-2.5-flash",
                "choices": [
                    {
                        "message": {"content": json.dumps(whole, ensure_ascii=False)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5000,
                    "completion_tokens": 3000,
                    "cost": 0.009,
                },
            },
        )

    client = OpenRouterClient("sk-test", transport=httpx.MockTransport(handler))
    service = HimeraExtractionService(client, WHOLE)
    result = await service.extract(synthetic_pdf())
    assert summary(result) == EXPECTED
    assert result.usage.cost_usd == pytest.approx(0.009)
    assert result.usage.prompt_tokens == 5001
    file_part = sent[0]["messages"][1]["content"][1]
    assert file_part["type"] == "file"
    assert sent[0]["response_format"]["json_schema"]["name"] == "himera_records"
