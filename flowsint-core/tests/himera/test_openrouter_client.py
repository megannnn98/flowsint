import json

import httpx
import pytest

from flowsint_core.himera.openrouter import (
    BudgetExceededError,
    CostBudget,
    ModelInfo,
    ModelNotFoundError,
    OpenRouterClient,
    OpenRouterHTTPError,
    OpenRouterRetryExhausted,
    pdf_file_part,
)

MODEL = ModelInfo(
    id="test/model",
    context_length=100000,
    prompt_price=1e-6,
    completion_price=2e-6,
    input_modalities=["text", "file"],
    supported_parameters=["structured_outputs", "response_format", "max_tokens"],
)


def ok_completion(content: str = '{"ok": true}', finish="stop", cost=0.001):
    return {
        "model": "test/model",
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": cost},
    }


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


async def no_sleep(_):
    return None


def make_client(recorder, **kw):
    return OpenRouterClient(
        "sk-test",
        transport=httpx.MockTransport(recorder),
        sleep=no_sleep,
        backoff_base_s=0,
        **kw,
    )


async def call(client, budget=None, model=MODEL, **kw):
    return await client.chat_json(
        model=model,
        messages=[{"role": "user", "content": "x"}],
        schema_name="s",
        schema={"type": "object"},
        max_tokens=100,
        budget=budget or CostBudget(1.0),
        estimated_prompt_tokens=1000,
        **kw,
    )


@pytest.mark.asyncio
async def test_success_sends_schema_plugin_and_records_usage():
    rec = Recorder([httpx.Response(200, json=ok_completion())])
    budget = CostBudget(1.0)
    result = await call(make_client(rec), budget=budget, pdf_engine="pdf-text")

    sent = json.loads(rec.requests[0].content)
    assert rec.requests[0].url.path.endswith("/chat/completions")
    assert rec.requests[0].headers["authorization"] == "Bearer sk-test"
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["plugins"] == [{"id": "file-parser", "pdf": {"engine": "pdf-text"}}]
    assert sent["temperature"] == 0
    assert result.content == '{"ok": true}'
    assert result.usage.prompt_tokens == 100
    assert result.usage.cost_usd == pytest.approx(0.001)
    assert budget.spent_usd == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_json_object_fallback_when_no_structured_outputs():
    rec = Recorder([httpx.Response(200, json=ok_completion())])
    model = ModelInfo("m", 1, 0, 0, ["text"], ["response_format"])
    await call(make_client(rec), model=model)
    sent = json.loads(rec.requests[0].content)
    assert sent["response_format"] == {"type": "json_object"}
    assert "plugins" not in sent


@pytest.mark.asyncio
async def test_retries_on_429_and_5xx_then_succeeds():
    rec = Recorder(
        [
            httpx.Response(429, json={"error": {"message": "rate"}}),
            httpx.Response(503, json={"error": {"message": "down"}}),
            httpx.Response(200, json=ok_completion()),
        ]
    )
    result = await call(make_client(rec, max_retries=3))
    assert result.finish_reason == "stop"
    assert len(rec.requests) == 3


@pytest.mark.asyncio
async def test_retries_on_timeout_and_gives_up():
    rec = Recorder([httpx.ReadTimeout("t")] * 3)
    with pytest.raises(OpenRouterRetryExhausted):
        await call(make_client(rec, max_retries=2))
    assert len(rec.requests) == 3


@pytest.mark.asyncio
async def test_client_error_is_not_retried():
    rec = Recorder([httpx.Response(401, json={"error": {"message": "bad key"}})])
    with pytest.raises(OpenRouterHTTPError) as exc:
        await call(make_client(rec))
    assert exc.value.status == 401
    assert len(rec.requests) == 1


@pytest.mark.asyncio
async def test_error_in_200_body_is_raised():
    rec = Recorder(
        [httpx.Response(200, json={"error": {"code": 400, "message": "no pdf"}})]
    )
    with pytest.raises(OpenRouterHTTPError):
        await call(make_client(rec))


@pytest.mark.asyncio
async def test_budget_blocks_call_before_sending():
    rec = Recorder([])
    budget = CostBudget(max_usd=0.0001)
    with pytest.raises(BudgetExceededError):
        await call(make_client(rec), budget=budget)
    assert rec.requests == []


@pytest.mark.asyncio
async def test_cost_computed_from_pricing_when_missing():
    body = ok_completion()
    del body["usage"]["cost"]
    rec = Recorder([httpx.Response(200, json=body)])
    result = await call(make_client(rec))
    assert result.usage.cost_usd == pytest.approx(100 * 1e-6 + 20 * 2e-6)


@pytest.mark.asyncio
async def test_get_model_reads_capabilities():
    models = {
        "data": [
            {
                "id": "a/b",
                "context_length": 1000,
                "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                "architecture": {"input_modalities": ["text", "file"]},
                "supported_parameters": ["structured_outputs"],
            }
        ]
    }
    rec = Recorder([httpx.Response(200, json=models)])
    client = make_client(rec)
    info = await client.get_model("a/b")
    assert info.accepts_files and info.structured_mode == "json_schema"
    assert info.prompt_price == pytest.approx(1e-6)
    with pytest.raises(ModelNotFoundError):
        await client.get_model("missing/model")
    assert len(rec.requests) == 1  # cached


def test_pdf_file_part_is_data_url():
    part = pdf_file_part(b"%PDF-1.4", "r.pdf")
    assert part["type"] == "file"
    assert part["file"]["file_data"].startswith("data:application/pdf;base64,")


def test_missing_key_rejected():
    with pytest.raises(ValueError):
        OpenRouterClient("")
