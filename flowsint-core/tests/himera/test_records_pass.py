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
    perfect_entities_handler,
    perfect_records_handler,
    synthetic_pdf,
)

WHOLE = HimeraSettings(pages_per_chunk=0, max_cost_usd=1.0, input_mode="pdf")


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
    assert result.usage.calls == 3 and result.usage.model == "test/model"


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
    service = HimeraExtractionService(
        client, replace(WHOLE, model="google/gemini-2.5-flash")
    )
    result = await service.extract(synthetic_pdf())
    assert summary(result) == EXPECTED
    assert result.usage.cost_usd == pytest.approx(0.009)
    assert result.usage.prompt_tokens == 5002
    file_part = sent[0]["messages"][1]["content"][1]
    assert file_part["type"] == "file"
    assert sent[0]["response_format"]["json_schema"]["name"] == "himera_records"


@pytest.mark.asyncio
async def test_overlap_page_with_miscounted_ordinals_gives_no_duplicates():
    """Seen on the real report: on an overlap page that starts with the tail of a
    previous record, the model counted that tail as record #1, shifting the
    ordinals of the records that really start there."""
    perfect = perfect_records_handler()

    def miscounting(schema_name, messages):
        out = perfect(schema_name, messages)
        if any(r["starts_before_range"] for r in out["records"]):
            for r in out["records"]:
                if r["page_start"] == 1 and not r["starts_before_range"]:
                    r["ordinal_on_start_page"] += 1
        return out

    settings = replace(WHOLE, pages_per_chunk=2, chunk_overlap_pages=1)
    result, _ = await run({"himera_records": miscounting}, settings)
    assert summary(result) == EXPECTED
    assert not any("already holds" in w for w in result.warnings)


def test_tail_on_overlap_page_joins_the_record_started_before_the_range():
    from flowsint_core.himera.pdf import PageRange
    from flowsint_core.himera.records import ChunkRecords, merge_chunk_records
    from flowsint_core.himera.schemas import LlmRecordsOutput

    def rec(heading, start, end, ordinal, fields, **kw):
        return {
            "section": "dataset_record",
            "source_heading": heading,
            "page_start": start,
            "page_end": end,
            "ordinal_on_start_page": ordinal,
            "fields": [{"name": n, "value": v} for n, v in fields],
            **kw,
        }

    a = LlmRecordsOutput(
        records=[
            rec("A", 1, 2, 1, [("x", "1"), ("y", "2")]),
            rec("B", 2, 2, 1, [("z", "3")], continues_after_range=True),
        ]
    )
    b = LlmRecordsOutput(
        records=[
            rec(
                "<continuation>",
                1,
                1,
                1,
                [("y", "2"), ("w", "4")],
                starts_before_range=True,
            ),
            rec("B", 1, 2, 2, [("z", "3"), ("v", "5")]),
        ]
    )
    merged = merge_chunk_records(
        [ChunkRecords(PageRange(1, 2), a), ChunkRecords(PageRange(2, 3), b)], 3
    )
    by_heading = {r.source_heading: r for r in merged.records}
    assert [r.record_id for r in merged.records] == ["p01-01", "p02-01"]
    assert [f.name for f in by_heading["A"].fields] == ["x", "y", "w"]
    assert [f.name for f in by_heading["B"].fields] == ["z", "v"]
    assert (by_heading["B"].page_end, by_heading["B"].open_ended) == (3, False)


@pytest.mark.asyncio
async def test_ranges_and_batches_run_in_parallel_within_limit():
    settings = replace(
        WHOLE,
        pages_per_chunk=1,
        chunk_overlap_pages=0,
        records_per_batch=2,
        concurrency=3,
    )
    fake = FakeOpenRouter(
        {
            "himera_records": perfect_records_handler(),
            "himera_entities": perfect_entities_handler(),
        },
        delay_s=0.02,
    )
    result = await HimeraExtractionService(fake, settings).extract(synthetic_pdf())
    assert summary(result) == EXPECTED
    assert fake.max_in_flight == 3


@pytest.mark.asyncio
async def test_parallel_calls_never_overshoot_the_budget():
    settings = replace(
        WHOLE,
        pages_per_chunk=1,
        chunk_overlap_pages=0,
        concurrency=8,
        max_cost_usd=0.02,
    )
    fake = FakeOpenRouter({"himera_records": perfect_records_handler()}, delay_s=0.02)
    result = await HimeraExtractionService(fake, settings).extract(synthetic_pdf())
    # each call reserves ~0.0068: only 2 fit at once in 0.02, others are refused
    assert len(fake.calls) == 2
    assert not result.complete
    assert any("budget exceeded" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_api_error_in_one_range_cancels_the_others():
    from flowsint_core.himera.openrouter import OpenRouterHTTPError

    perfect = perfect_records_handler()

    def failing(schema_name, messages):
        if "pages 1-1" in messages[1]["content"][0]["text"]:
            return OpenRouterHTTPError(401, "bad key")
        return perfect(schema_name, messages)

    settings = replace(WHOLE, pages_per_chunk=1, chunk_overlap_pages=0, concurrency=8)
    fake = FakeOpenRouter({"himera_records": failing}, delay_s=0.05)
    with pytest.raises(OpenRouterHTTPError):
        await HimeraExtractionService(fake, settings).extract(synthetic_pdf())


def _rec(heading, start, end, ordinal, fields, **kw):
    return {
        "section": "dataset_record",
        "source_heading": heading,
        "page_start": start,
        "page_end": end,
        "ordinal_on_start_page": ordinal,
        "fields": [{"name": n, "value": v} for n, v in fields],
        **kw,
    }


def _merge(*chunks, pages):
    from flowsint_core.himera.pdf import PageRange
    from flowsint_core.himera.records import ChunkRecords, merge_chunk_records
    from flowsint_core.himera.schemas import LlmRecordsOutput

    return merge_chunk_records(
        [
            ChunkRecords(PageRange(a, b), LlmRecordsOutput(records=r))
            for (a, b), r in chunks
        ],
        pages,
    )


def test_wrapped_heading_read_differently_on_overlap_page_is_one_record():
    """Real report p.13: 'РФ КРЕДИТ УЧЕТНЫЙ…' in one range, 'КРЕДИТ УЧЕТНЫЙ…' in the next."""
    merged = _merge(
        (
            (1, 2),
            [
                _rec(
                    "РФ КРЕДИТ УЧЕТНЫЙ КОНТИНГЕНТ 2016 09",
                    2,
                    2,
                    1,
                    [("a", "1")],
                    continues_after_range=True,
                )
            ],
        ),
        (
            (2, 3),
            [
                _rec(
                    "КРЕДИТ УЧЕТНЫЙ КОНТИНГЕНТ 2016 09",
                    1,
                    2,
                    1,
                    [("a", "1"), ("b", "2")],
                )
            ],
        ),
        pages=3,
    )
    assert [
        (r.record_id, r.page_start, r.page_end, r.open_ended) for r in merged.records
    ] == [("p02-01", 2, 3, False)]


def test_similar_headings_with_other_periods_stay_apart():
    from flowsint_core.himera.records import similar_headings

    assert similar_headings("РФ КРЕДИТ УЧЕТНЫЙ 2016", "КРЕДИТ УЧЕТНЫЙ 2016")
    assert not similar_headings("ЖИТЕЛИ РОССИИ 2010 03", "ЖИТЕЛИ РОССИИ 2010 07")


def test_only_first_record_of_a_range_can_be_a_tail():
    """Real report pp.22-23: later records flagged as continuation were glued to p.21."""
    merged = _merge(
        ((1, 2), [_rec("A", 1, 2, 1, [("x", "1")], continues_after_range=True)]),
        (
            (2, 3),
            [
                _rec("<continuation>", 1, 1, 1, [("y", "2")], starts_before_range=True),
                _rec("B", 2, 2, 1, [("z", "3")], starts_before_range=True),
                _rec("C", 2, 2, 2, [("w", "4")], starts_before_range=True),
            ],
        ),
        pages=3,
    )
    by = {r.source_heading: r for r in merged.records}
    assert sorted(by) == ["A", "B", "C"]
    assert [f.name for f in by["A"].fields] == ["x", "y"]
    assert by["B"].page_start == 3 and by["C"].page_start == 3


@pytest.mark.asyncio
async def test_progress_is_reported_per_range_and_batch():
    events = []
    settings = replace(
        WHOLE, pages_per_chunk=1, chunk_overlap_pages=0, records_per_batch=4
    )
    fake = FakeOpenRouter(
        {
            "himera_records": perfect_records_handler(),
            "himera_entities": perfect_entities_handler(),
        }
    )
    await HimeraExtractionService(fake, settings, on_progress=events.append).extract(
        synthetic_pdf()
    )
    records = [e for e in events if e["stage"] == "records"]
    entities = [e for e in events if e["stage"] == "entities"]
    assert records[0] == {**records[0], "done": 0, "total": 4}
    assert records[-1]["done"] == records[-1]["total"] == 4
    assert entities[-1]["done"] == entities[-1]["total"] == 3  # 10 records / 4
    assert events[-1]["stage"] == "validating"
    assert events[-1]["cost_usd"] > 0 and events[-1]["calls"] == 7


@pytest.mark.asyncio
async def test_progress_total_grows_when_a_range_is_split():
    events = []
    perfect = perfect_records_handler()

    def handler(schema_name, messages):
        if "pages 1-4" in messages[1]["content"][0]["text"]:
            return ChatResult('{"records": [', "length", Usage(1, 1, 0.0, 1), "m")
        return perfect(schema_name, messages)

    await HimeraExtractionService(
        FakeOpenRouter({"himera_records": handler}), WHOLE, on_progress=events.append
    ).extract(synthetic_pdf())
    records = [e for e in events if e["stage"] == "records"]
    assert records[-1]["done"] == records[-1]["total"] == 2


@pytest.mark.asyncio
async def test_broken_progress_callback_does_not_break_extraction():
    def boom(_):
        raise RuntimeError("db down")

    fake = FakeOpenRouter({"himera_records": perfect_records_handler()})
    result = await HimeraExtractionService(fake, WHOLE, on_progress=boom).extract(
        synthetic_pdf()
    )
    assert len(result.records) == 11
