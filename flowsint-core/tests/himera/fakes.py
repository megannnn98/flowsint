"""Test doubles: a scripted OpenRouter client that never touches the network."""

import asyncio
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


def is_text_mode(messages: List[Dict[str, Any]]) -> bool:
    return isinstance(messages[1]["content"], str)


def page_range_of(messages: List[Dict[str, Any]]):
    content = messages[1]["content"]
    text = content if isinstance(content, str) else content[0]["text"]
    m = re.search(r"pages (\d+)-(\d+) of a (\d+)-page", text)
    assert m, text
    return tuple(int(x) for x in m.groups())


def perfect_records_handler(whole: Optional[Dict[str, Any]] = None) -> Handler:
    whole = whole or load_json("llm_records_whole.json")

    def handler(schema_name, messages):
        first, last, total = page_range_of(messages)
        out = simulate_records_chunk(whole, first, last, total)
        if is_text_mode(messages):  # text mode answers with report pages
            for r in out["records"]:
                r["page_start"] += first - 1
                r["page_end"] += first - 1
        return out

    return handler


def empty_entities_handler(schema_name, messages):
    return {"entities": [], "relationships": []}


class FakeOpenRouter:
    def __init__(
        self,
        handlers: Dict[str, Handler],
        model: ModelInfo = MODEL,
        cost_per_call: float = 0.001,
        delay_s: float = 0.0,
    ):
        self.delay_s = delay_s
        self.in_flight = 0
        self.max_in_flight = 0
        self.handlers = {"himera_entities": empty_entities_handler, **handlers}
        self.model = model
        self.cost_per_call = cost_per_call
        self.calls: List[Dict[str, Any]] = []

    def calls_for(self, schema_name: str) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["schema_name"] == schema_name]

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
        budget.reserve(estimate)
        self.calls.append({"schema_name": schema_name, "messages": messages, **kw})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
        finally:
            self.in_flight -= 1
        if schema_name in self.handlers and messages[0]["content"].startswith(
            "You fix JSON"
        ):
            handler = self.handlers.get("repair", self.handlers[schema_name])
        else:
            handler = self.handlers[schema_name]
        reply = handler(schema_name, messages)
        if isinstance(reply, Exception):
            budget.release(estimate)
            raise reply
        budget.settle(estimate, self.cost_per_call)
        if isinstance(reply, ChatResult):
            return reply
        content = (
            reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        )
        return ChatResult(
            content, "stop", Usage(1000, 200, self.cost_per_call, 1), model.id
        )


def batch_records_of(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    text = messages[1]["content"]
    return json.loads(text.split("\n\nRecords:\n", 1)[1])


def perfect_entities_handler(by_record: Optional[Dict[str, Any]] = None) -> Handler:
    """Answer of a perfect model for whatever records are in the batch."""
    by_record = by_record or load_json("llm_entities_by_record.json")

    def handler(schema_name, messages):
        entities, relationships = [], []
        for rec in batch_records_of(messages):
            gold = by_record.get(rec["record_id"])
            if not gold:
                continue
            rid = rec["record_id"]
            for e in gold["entities"]:
                entities.append(
                    {**e, "entity_id": f"{rid}.{e['entity_id']}", "record_id": rid}
                )
            for r in gold["relationships"]:
                relationships.append(
                    {
                        **r,
                        "source_entity_id": f"{rid}.{r['source_entity_id']}",
                        "target_entity_id": f"{rid}.{r['target_entity_id']}",
                        "record_id": rid,
                    }
                )
        return {"entities": entities, "relationships": relationships}

    return handler
