"""
HimeraExtractionService: PDF in, ``HimeraExtractionResult`` out.

Pass 1 (records) sends the PDF (whole or by overlapping page ranges) to
OpenRouter; pass 2 (entities) sends record JSON only. All semantic work is done
by the LLM; this module only orchestrates calls, parses/validates JSON and
merges deterministic pieces.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .config import HimeraSettings
from .openrouter import (
    BudgetExceededError,
    CostBudget,
    ModelCapabilityError,
    ModelInfo,
    OpenRouterClient,
    Usage,
    pdf_file_part,
)
from .pdf import (
    TOKENS_PER_PAGE_ESTIMATE,
    PageRange,
    PreparedPdf,
    extract_pages,
    plan_page_ranges,
    prepare_pdf,
    split_range,
)
from .prompts import (
    RECORDS_SYSTEM,
    REPAIR_SYSTEM,
    records_user_prompt,
    repair_user_prompt,
)
from .records import ChunkRecords, MergeResult, merge_chunk_records
from .schemas import (
    RECORDS_SCHEMA,
    HimeraExtractionResult,
    LlmRecordsOutput,
    UsageSummary,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_MAX_SPLIT_DEPTH = 3
_SYSTEM_PROMPT_TOKENS = 1500


@dataclass
class CallOutcome:
    parsed: Optional[BaseModel]
    truncated: bool = False
    error: Optional[str] = None


class _Run:
    """Mutable state of one extraction (budget, usage, warnings)."""

    def __init__(self, model: ModelInfo, engine: str, settings: HimeraSettings):
        self.model = model
        self.engine = engine
        self.budget = CostBudget(settings.max_cost_usd)
        self.usage = Usage()
        self.warnings: List[str] = []
        self.complete = True
        self.stopped = False  # budget exhausted: no further calls


def parse_json_output(content: str, output_model: Type[T]) -> T:
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    return output_model.model_validate(json.loads(text))


class HimeraExtractionService:
    def __init__(
        self, client: OpenRouterClient, settings: Optional[HimeraSettings] = None
    ):
        self._client = client
        self._settings = settings or HimeraSettings.from_env()

    # ------------------------------------------------------------- public

    async def extract(
        self, pdf_bytes: bytes, filename: str = "report.pdf"
    ) -> HimeraExtractionResult:
        prepared = prepare_pdf(pdf_bytes)
        model = await self._client.get_model(self._settings.model)
        engine = self._resolve_engine(prepared, model)
        run = _Run(model, engine, self._settings)
        if model.structured_mode != "json_schema":
            run.warnings.append(
                f"model {model.id} has no strict structured outputs; using "
                f"{model.structured_mode} mode with schema in the prompt"
            )
        logger.info(
            "himera extraction start sha256=%s pages=%s text_layer=%s model=%s engine=%s",
            prepared.sha256[:12],
            prepared.page_count,
            prepared.has_text_layer,
            model.id,
            engine,
        )

        merged = await self.extract_records(prepared, run, filename)
        if not merged.records:
            run.complete = False
            run.warnings.append("no records extracted")

        result = HimeraExtractionResult(
            report_id=merged.report_id,
            pdf_sha256=prepared.sha256,
            page_count=prepared.page_count,
            records=merged.records,
            warnings=run.warnings + merged.warnings,
            truncation_notice=merged.truncation_notice,
            complete=run.complete,
            usage=self._usage_summary(run),
        )
        if merged.truncation_notice:
            result.warnings.append(
                "the PDF states it may be truncated; the TXT version can contain more records"
            )
        return result

    # ------------------------------------------------------------- pass 1

    async def extract_records(
        self, prepared: PreparedPdf, run: _Run, filename: str
    ) -> MergeResult:
        ranges = plan_page_ranges(
            prepared.page_count,
            self._settings.pages_per_chunk,
            self._settings.chunk_overlap_pages,
        )
        chunks: List[ChunkRecords] = []
        for rng in ranges:
            chunks.extend(
                await self._records_for_range(prepared, rng, run, filename, 0)
            )
        return merge_chunk_records(chunks, prepared.page_count)

    async def _records_for_range(
        self,
        prepared: PreparedPdf,
        rng: PageRange,
        run: _Run,
        filename: str,
        depth: int,
    ) -> List[ChunkRecords]:
        if run.stopped:
            return []
        whole = rng.first == 1 and rng.last == prepared.page_count
        data = prepared.data if whole else extract_pages(prepared.data, rng)
        messages = [
            {
                "role": "system",
                "content": self._system(RECORDS_SYSTEM, RECORDS_SCHEMA, run),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": records_user_prompt(
                            rng.first, rng.last, prepared.page_count
                        ),
                    },
                    pdf_file_part(data, filename),
                ],
            },
        ]
        extra_cost = (
            rng.pages * self._settings.ocr_usd_per_page
            if run.engine == "mistral-ocr"
            else 0.0
        )
        outcome = await self._structured_call(
            run,
            messages,
            "himera_records",
            RECORDS_SCHEMA,
            LlmRecordsOutput,
            estimated_prompt_tokens=rng.pages * TOKENS_PER_PAGE_ESTIMATE
            + _SYSTEM_PROMPT_TOKENS,
            pdf_engine=run.engine,
            extra_cost_estimate_usd=extra_cost,
            what=f"records {rng}",
        )
        if outcome.truncated:
            if rng.pages > 1 and depth < _MAX_SPLIT_DEPTH:
                left, right = split_range(rng)
                run.warnings.append(
                    f"records {rng}: response hit the token limit, retrying as {left} and {right}"
                )
                return await self._records_for_range(
                    prepared, left, run, filename, depth + 1
                ) + await self._records_for_range(
                    prepared, right, run, filename, depth + 1
                )
            run.complete = False
            run.warnings.append(
                f"records {rng}: response truncated, records may be missing"
            )
        if outcome.parsed is None:
            if outcome.error:
                run.complete = False
                run.warnings.append(f"records {rng}: {outcome.error}")
            return []
        output = outcome.parsed
        assert isinstance(output, LlmRecordsOutput)
        if not output.records:
            run.warnings.append(f"records {rng}: model returned no records")
        return [ChunkRecords(rng, output)]

    # ------------------------------------------------------------ helpers

    def _resolve_engine(self, prepared: PreparedPdf, model: ModelInfo) -> str:
        engine = self._settings.pdf_engine
        if engine == "auto":
            engine = "pdf-text" if prepared.has_text_layer else "mistral-ocr"
        if engine == "native" and not model.accepts_files:
            raise ModelCapabilityError(
                f"model {model.id} does not accept PDF files natively; "
                "use HIMERA_PDF_ENGINE=pdf-text or mistral-ocr"
            )
        return engine

    def _system(self, prompt: str, schema: Dict[str, Any], run: _Run) -> str:
        if run.model.structured_mode == "json_schema":
            return prompt
        return (
            f"{prompt}\n\nThe JSON must match this JSON Schema:\n{json.dumps(schema)}"
        )

    async def _structured_call(
        self,
        run: _Run,
        messages: List[Dict[str, Any]],
        schema_name: str,
        schema: Dict[str, Any],
        output_model: Type[BaseModel],
        *,
        estimated_prompt_tokens: int,
        what: str,
        pdf_engine: Optional[str] = None,
        extra_cost_estimate_usd: float = 0.0,
    ) -> CallOutcome:
        try:
            result = await self._client.chat_json(
                model=run.model,
                messages=messages,
                schema_name=schema_name,
                schema=schema,
                max_tokens=self._settings.max_output_tokens,
                budget=run.budget,
                estimated_prompt_tokens=estimated_prompt_tokens,
                pdf_engine=pdf_engine,
                extra_cost_estimate_usd=extra_cost_estimate_usd,
            )
        except BudgetExceededError as e:
            self._stop(run, what, e)
            return CallOutcome(None)
        run.usage.add(result.usage)
        if result.finish_reason == "length":
            return CallOutcome(None, truncated=True)
        try:
            return CallOutcome(parse_json_output(result.content, output_model))
        except (ValueError, ValidationError) as e:
            error = _short_error(e)
        logger.warning("himera %s: invalid JSON from model: %s", what, error)

        broken = result.content
        for attempt in range(self._settings.max_repair_attempts):
            if run.stopped:
                break
            try:
                repaired = await self._client.chat_json(
                    model=run.model,
                    messages=[
                        {"role": "system", "content": REPAIR_SYSTEM},
                        {
                            "role": "user",
                            "content": repair_user_prompt(broken, error, schema),
                        },
                    ],
                    schema_name=schema_name,
                    schema=schema,
                    max_tokens=self._settings.max_output_tokens,
                    budget=run.budget,
                    estimated_prompt_tokens=len(broken) // 2 + _SYSTEM_PROMPT_TOKENS,
                )
            except BudgetExceededError as e:
                self._stop(run, what, e)
                break
            run.usage.add(repaired.usage)
            try:
                parsed = parse_json_output(repaired.content, output_model)
                run.warnings.append(
                    f"{what}: model JSON repaired (attempt {attempt + 1})"
                )
                return CallOutcome(parsed)
            except (ValueError, ValidationError) as e:
                broken, error = repaired.content, _short_error(e)
        return CallOutcome(None, error=f"invalid JSON after repair: {error}")

    def _stop(self, run: _Run, what: str, error: Exception) -> None:
        run.stopped = True
        run.complete = False
        run.warnings.append(f"{what}: {error}")
        logger.warning("himera %s: budget exceeded, stopping", what)

    def _usage_summary(self, run: _Run) -> UsageSummary:
        return UsageSummary(
            model=run.model.id,
            pdf_engine=run.engine,
            calls=run.usage.calls,
            prompt_tokens=run.usage.prompt_tokens,
            completion_tokens=run.usage.completion_tokens,
            cost_usd=round(run.usage.cost_usd, 6),
        )


def _short_error(e: Exception) -> str:
    # Pydantic errors can echo input values (personal data); keep only locations/types.
    if isinstance(e, ValidationError):
        parts = [
            f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}"
            for err in e.errors()[:10]
        ]
        return "; ".join(parts)
    if isinstance(e, json.JSONDecodeError):
        return f"JSONDecodeError at line {e.lineno} col {e.colno}: {e.msg}"
    return type(e).__name__
