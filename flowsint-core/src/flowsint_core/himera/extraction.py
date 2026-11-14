"""
HimeraExtractionService: PDF in, ``HimeraExtractionResult`` out.

Pass 1 (records) sends the PDF (whole or by overlapping page ranges) to
OpenRouter; pass 2 (entities) sends record JSON only. All semantic work is done
by the LLM; this module only orchestrates calls, parses/validates JSON and
merges deterministic pieces.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Type, TypeVar

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
    page_texts,
    plan_page_ranges,
    prepare_pdf,
    split_range,
    text_with_markers,
)
from .prompts import (
    ENTITIES_SYSTEM,
    RECORDS_SYSTEM,
    REPAIR_SYSTEM,
    entities_user_prompt,
    records_text_prompt,
    records_user_prompt,
    repair_user_prompt,
)
from .records import ChunkRecords, MergeResult, merge_chunk_records
from .schemas import (
    ENTITIES_SCHEMA,
    RECORDS_SCHEMA,
    HimeraExtractionResult,
    LlmEntitiesOutput,
    LlmEntity,
    LlmRecordsOutput,
    LlmRelationship,
    Section,
    SourceRecord,
    UsageSummary,
)
from .validation import assemble

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
        # Independent ranges/batches run in parallel, at most this many calls at once.
        self.slots = asyncio.Semaphore(max(1, settings.concurrency))
        self.page_texts: List[str] = []  # text mode only
        self.progress: Dict[str, Any] = {"stage": "preparing", "done": 0, "total": 0}


TEXT_ENGINE = "text"


def _to_relative_pages(output: LlmRecordsOutput, rng: PageRange) -> LlmRecordsOutput:
    """Text mode answers with report pages (from the markers); the merge works
    with pages relative to the range. Out-of-range pages are left for the merge
    to clamp and report."""

    def rel(page: int) -> int:
        return page - rng.first + 1 if rng.first <= page <= rng.last else page

    return output.model_copy(
        update={
            "records": [
                r.model_copy(
                    update={
                        "page_start": rel(r.page_start),
                        "page_end": rel(r.page_end),
                    }
                )
                for r in output.records
            ]
        }
    )


async def _gather(coros: List[Awaitable[Any]]) -> List[Any]:
    """Run concurrently, keep order; on the first failure cancel the rest (no
    orphan calls keep spending money) and re-raise that failure as is."""
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(c) for c in coros]  # type: ignore[arg-type]
    except BaseExceptionGroup as eg:
        raise eg.exceptions[0]
    return [t.result() for t in tasks]


def parse_json_output(content: str, output_model: Type[T]) -> T:
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    return output_model.model_validate(json.loads(text))


ProgressCallback = Callable[[Dict[str, Any]], None]


class HimeraExtractionService:
    def __init__(
        self,
        client: OpenRouterClient,
        settings: Optional[HimeraSettings] = None,
        on_progress: Optional[ProgressCallback] = None,
    ):
        self._client = client
        self._settings = settings or HimeraSettings.from_env()
        # Called with {"stage", "done", "total", "calls", "cost_usd"} after every
        # finished range/batch. Must be cheap; errors in it are logged and ignored.
        self._on_progress = on_progress

    # ----------------------------------------------------------- progress

    def _stage(self, run: "_Run", stage: str, total: int) -> None:
        run.progress = {"stage": stage, "done": 0, "total": total}
        self._report(run)

    def _grow(self, run: "_Run") -> None:
        # One unit was split in two (truncated answer).
        run.progress["total"] = run.progress.get("total", 0) + 1
        self._report(run)

    def _tick(self, run: "_Run") -> None:
        run.progress["done"] = run.progress.get("done", 0) + 1
        self._report(run)

    def _report(self, run: "_Run") -> None:
        if self._on_progress is None:
            return
        try:
            self._on_progress(
                {
                    **run.progress,
                    "calls": run.usage.calls,
                    "cost_usd": round(run.usage.cost_usd, 4),
                }
            )
        except Exception as e:  # noqa: BLE001 - progress must never break extraction
            logger.warning("himera progress callback failed: %s", type(e).__name__)

    # ------------------------------------------------------------- public

    async def extract(
        self, pdf_bytes: bytes, filename: str = "report.pdf"
    ) -> HimeraExtractionResult:
        prepared = prepare_pdf(pdf_bytes)
        model = await self._client.get_model(self._settings.model)
        engine = self._resolve_engine(prepared, model)
        run = _Run(model, engine, self._settings)
        if engine == TEXT_ENGINE:
            run.page_texts = page_texts(prepared.data)
        elif self._settings.input_mode == "text":
            run.warnings.append(
                "PDF has no text layer: sent as a file for OCR instead of text"
            )
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

        entities, relationships = await self.extract_entities(merged.records, run)
        self._stage(run, "validating", 1)
        assembled = assemble(merged.records, entities, relationships)

        result = HimeraExtractionResult(
            report_id=merged.report_id,
            pdf_sha256=prepared.sha256,
            page_count=prepared.page_count,
            records=merged.records,
            entities=assembled.entities,
            unverified_entities=assembled.unverified_entities,
            relationships=assembled.relationships,
            ambiguous_relationships=assembled.ambiguous_relationships,
            rejected=assembled.rejected,
            warnings=run.warnings + merged.warnings + assembled.warnings,
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
        self._stage(run, "records", len(ranges))
        results = await _gather(
            [self._records_for_range(prepared, rng, run, filename, 0) for rng in ranges]
        )
        chunks: List[ChunkRecords] = [c for part in results for c in part]
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
        system = {
            "role": "system",
            "content": self._system(RECORDS_SYSTEM, RECORDS_SCHEMA, run),
        }
        text_mode = run.engine == TEXT_ENGINE
        if text_mode:
            text = text_with_markers(run.page_texts, rng)
            messages = [
                system,
                {
                    "role": "user",
                    "content": records_text_prompt(
                        rng.first, rng.last, prepared.page_count, text
                    ),
                },
            ]
            estimated = len(text) // 2 + _SYSTEM_PROMPT_TOKENS
        else:
            whole = rng.first == 1 and rng.last == prepared.page_count
            data = prepared.data if whole else extract_pages(prepared.data, rng)
            messages = [
                system,
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
            estimated = rng.pages * TOKENS_PER_PAGE_ESTIMATE + _SYSTEM_PROMPT_TOKENS
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
            estimated_prompt_tokens=estimated,
            pdf_engine=None if text_mode else run.engine,
            extra_cost_estimate_usd=extra_cost,
            what=f"records {rng}",
        )
        will_split = outcome.truncated and rng.pages > 1 and depth < _MAX_SPLIT_DEPTH
        if will_split:
            self._grow(run)
        else:
            self._tick(run)
        if outcome.truncated:
            if will_split:
                left, right = split_range(rng)
                run.warnings.append(
                    f"records {rng}: response hit the token limit, retrying as {left} and {right}"
                )
                halves = await _gather(
                    [
                        self._records_for_range(
                            prepared, left, run, filename, depth + 1
                        ),
                        self._records_for_range(
                            prepared, right, run, filename, depth + 1
                        ),
                    ]
                )
                return halves[0] + halves[1]
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
        if text_mode:
            output = _to_relative_pages(output, rng)
        if not output.records:
            run.warnings.append(f"records {rng}: model returned no records")
        return [ChunkRecords(rng, output)]

    # ------------------------------------------------------------- pass 2

    async def extract_entities(
        self, records: List[SourceRecord], run: _Run
    ) -> Tuple[List[LlmEntity], List[LlmRelationship]]:
        subject = next(
            (
                {
                    "source_heading": r.source_heading,
                    "fields": [f.model_dump() for f in r.fields],
                }
                for r in records
                if r.section == Section.SUMMARY
            ),
            {},
        )
        payload = [
            {
                "record_id": r.record_id,
                "section": r.section.value,
                "source_heading": r.source_heading,
                "source_period": r.source_period,
                "page_start": r.page_start,
                "page_end": r.page_end,
                "name_match_only": r.name_match_only,
                "fields": [f.model_dump() for f in r.fields],
            }
            for r in records
            if r.section != Section.SERVICE
        ]
        size = max(1, self._settings.records_per_batch)
        self._stage(run, "entities", (len(payload) + size - 1) // size)
        results = await _gather(
            [
                self._entities_for_batch(
                    subject, payload[start : start + size], run, f"b{n}"
                )
                for n, start in enumerate(range(0, len(payload), size), 1)
            ]
        )
        entities: List[LlmEntity] = [e for part in results for e in part[0]]
        relationships: List[LlmRelationship] = [r for part in results for r in part[1]]
        return entities, relationships

    async def _entities_for_batch(
        self,
        subject: Dict[str, Any],
        batch: List[Dict[str, Any]],
        run: _Run,
        batch_key: str,
    ) -> Tuple[List[LlmEntity], List[LlmRelationship]]:
        if run.stopped or not batch:
            return [], []
        ids = f"{batch[0]['record_id']}..{batch[-1]['record_id']}"
        user = entities_user_prompt(subject, batch)
        outcome = await self._structured_call(
            run,
            [
                {
                    "role": "system",
                    "content": self._system(ENTITIES_SYSTEM, ENTITIES_SCHEMA, run),
                },
                {"role": "user", "content": user},
            ],
            "himera_entities",
            ENTITIES_SCHEMA,
            LlmEntitiesOutput,
            estimated_prompt_tokens=len(user) // 2 + _SYSTEM_PROMPT_TOKENS,
            what=f"entities {ids}",
        )
        will_split = outcome.truncated and len(batch) > 1
        if will_split:
            self._grow(run)
        else:
            self._tick(run)
        if outcome.truncated:
            if will_split:
                half = len(batch) // 2
                run.warnings.append(
                    f"entities {ids}: response hit the token limit, retrying in two batches"
                )
                left, right = await _gather(
                    [
                        self._entities_for_batch(
                            subject, batch[:half], run, batch_key + "a"
                        ),
                        self._entities_for_batch(
                            subject, batch[half:], run, batch_key + "b"
                        ),
                    ]
                )
                return left[0] + right[0], left[1] + right[1]
            run.complete = False
            run.warnings.append(
                f"entities {ids}: response truncated, entities may be missing"
            )
        if outcome.parsed is None:
            if outcome.error:
                run.complete = False
                run.warnings.append(f"entities {ids}: {outcome.error}")
            return [], []
        output = outcome.parsed
        assert isinstance(output, LlmEntitiesOutput)
        # Entity ids are only unique inside one answer: namespace them per batch.
        prefix = f"{batch_key}:"
        allowed = {r["record_id"] for r in batch}
        entities = []
        for e in output.entities:
            e = e.model_copy(update={"entity_id": prefix + e.entity_id})
            if e.record_id not in allowed:
                # Points to a record outside this batch: keep it so validation rejects it.
                e = e.model_copy(update={"record_id": f"{e.record_id}?outside-batch"})
            entities.append(e)
        rels = [
            r.model_copy(
                update={
                    "source_entity_id": prefix + r.source_entity_id,
                    "target_entity_id": prefix + r.target_entity_id,
                }
            )
            for r in output.relationships
        ]
        return entities, rels

    # ------------------------------------------------------------ helpers

    def _resolve_engine(self, prepared: PreparedPdf, model: ModelInfo) -> str:
        if self._settings.input_mode != "pdf" and prepared.has_text_layer:
            return TEXT_ENGINE
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
            async with run.slots:
                result = await self._client.chat_json(
                    model=run.model,
                    messages=messages,
                    schema_name=schema_name,
                    schema=schema,
                    max_tokens=self._settings.max_output_tokens,
                    budget=run.budget,
                    provider_sort=self._settings.provider_sort or None,
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
                async with run.slots:
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
                        provider_sort=self._settings.provider_sort or None,
                        estimated_prompt_tokens=len(broken) // 2
                        + _SYSTEM_PROMPT_TOKENS,
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
