"""Test doubles: a scripted OpenRouter client that never touches the network."""

import copy
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from flowsint_core.himera.openrouter import ChatResult, CostBudget, ModelInfo, Usage

FIXTURES = Path(__file__).parent / "fixtures"

MODEL = ModelInfo(
    id="test/model",
    context_length=200000,
    prompt_price=1e-7,
    completion_price=4e-7,
    input_modalities=["text", "image", "file"],
    supported_parameters=["structured_outputs", "response_format", "max_tokens"],
)


def load_json(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def synthetic_pdf() -> bytes:
    return (FIXTURES / "synthetic_report.pdf").read_bytes()


def simulate_records_chunk(
    whole: Dict[str, Any], first: int, last: int, total: int
) -> Dict[str, Any]:
    """What a perfect model returns for pages first..last (relative page numbers)."""
    out: List[Dict[str, Any]] = []
    for rec in whole["records"]:
        start, end = rec["page_start"], rec["page_end"]
        if first <= start <= last:
            r = copy.deepcopy(rec)
            r["page_start"] = start - first + 1
            r["page_end"] = min(end, last) - first + 1
            r["continues_after_range"] = end > last
            out.append(r)
        elif start < first <= end:
            r = copy.deepcopy(rec)
            r.update(
                source_heading="<continuation>",
                starts_before_range=True,
                page_start=1,
                page_end=min(end, last) - first + 1,
                ordinal_on_start_page=1,
                continues_after_range=end > last,
            )
            out.insert(0, r)
    return {
        "report_id": whole["report_id"] if first == 1 else None,
        "truncation_notice": whole["truncation_notice"] and last == total,
        "records": out,
    }


Reply = Union[str, Dict[str, Any], ChatResult, Exception]
Handler = Callable[[str, List[Dict[str, Any]]], Reply]


def page_range_of(messages: List[Dict[str, Any]]):
    text = messages[1]["content"][0]["text"]
    m = re.search(r"pages (\d+)-(\d+) of a (\d+)-page", text)
    assert m, text
    return tuple(int(x) for x in m.groups())


def perfect_records_handler(whole: Optional[Dict[str, Any]] = None) -> Handler:
    whole = whole or load_json("llm_records_whole.json")

    def handler(schema_name, messages):
        first, last, total = page_range_of(messages)
        return simulate_records_chunk(whole, first, last, total)

    return handler


class FakeOpenRouter:
    def __init__(
        self,
        handlers: Dict[str, Handler],
        model: ModelInfo = MODEL,
        cost_per_call: float = 0.001,
    ):
        self.handlers = handlers
        self.model = model
        self.cost_per_call = cost_per_call
        self.calls: List[Dict[str, Any]] = []

    async def get_model(self, model_id: str) -> ModelInfo:
        return self.model

    async def chat_json(
        self, *, model, messages, schema_name, budget: CostBudget, **kw
    ):
        estimate = (
            kw["estimated_prompt_tokens"] * model.prompt_price
            + kw["max_tokens"] * model.completion_price
            + kw.get("extra_cost_estimate_usd", 0.0)
        )
        budget.check(estimate)
        self.calls.append({"schema_name": schema_name, "messages": messages, **kw})
        if schema_name in self.handlers and messages[0]["content"].startswith(
            "You fix JSON"
        ):
            handler = self.handlers.get("repair", self.handlers[schema_name])
        else:
            handler = self.handlers[schema_name]
        reply = handler(schema_name, messages)
        if isinstance(reply, Exception):
            raise reply
        budget.charge(self.cost_per_call)
        if isinstance(reply, ChatResult):
            return reply
        content = (
            reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        )
        return ChatResult(
            content, "stop", Usage(1000, 200, self.cost_per_call, 1), model.id
        )
