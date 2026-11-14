"""Runtime settings of the Himera PDF import (read from the environment)."""

import os
from dataclasses import dataclass
from typing import Literal, Mapping, Optional

PdfEngine = Literal["auto", "pdf-text", "mistral-ocr", "native"]
InputMode = Literal["auto", "text", "pdf"]

DEFAULT_MODEL = "deepseek/deepseek-v3.2"
_PDF_ENGINES = ("auto", "pdf-text", "mistral-ocr", "native")
_INPUT_MODES = ("auto", "text", "pdf")


@dataclass(frozen=True)
class HimeraSettings:
    model: str = DEFAULT_MODEL
    # "text": send the locally extracted text with page markers (any text model,
    # e.g. DeepSeek); "pdf": send the PDF file to OpenRouter's parser/OCR;
    # "auto": text when the PDF has a text layer, pdf (OCR) otherwise.
    input_mode: InputMode = "auto"
    # PDF mode only. "auto": pdf-text with a text layer, mistral-ocr otherwise.
    pdf_engine: PdfEngine = "auto"
    # Hard cap for one document (all LLM calls together), in USD.
    max_cost_usd: float = 0.50
    # Rough OCR price used only for the pre-call budget estimate.
    ocr_usd_per_page: float = 0.002
    timeout_s: float = 180.0
    max_retries: int = 3
    backoff_base_s: float = 2.0
    # Page ranges for the records pass. 0 = whole document in one call.
    pages_per_chunk: int = 4
    chunk_overlap_pages: int = 1
    # Records per call in the entities pass.
    records_per_batch: int = 8
    # Parallel OpenRouter calls (ranges and batches are independent).
    concurrency: int = 8
    # OpenRouter provider routing: "throughput" picks the fastest provider of the
    # model, "price" the cheapest, "latency" the quickest to first token, "" = default.
    provider_sort: str = "throughput"
    max_output_tokens: int = 16000
    # Bounded re-ask when the model returned broken / truncated JSON.
    max_repair_attempts: int = 1

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "HimeraSettings":
        env = os.environ if env is None else env
        engine = env.get("HIMERA_PDF_ENGINE", "auto")
        if engine not in _PDF_ENGINES:
            raise ValueError(
                f"HIMERA_PDF_ENGINE must be one of {', '.join(_PDF_ENGINES)}, got '{engine}'"
            )
        mode = env.get("HIMERA_INPUT") or "auto"
        if mode not in _INPUT_MODES:
            raise ValueError(
                f"HIMERA_INPUT must be one of {', '.join(_INPUT_MODES)}, got '{mode}'"
            )
        defaults = cls()

        def num(name: str, default: float, cast=float):
            raw = env.get(name)
            return default if raw in (None, "") else cast(raw)

        return cls(
            model=env.get("HIMERA_MODEL") or DEFAULT_MODEL,
            input_mode=mode,  # type: ignore[arg-type]
            pdf_engine=engine,  # type: ignore[arg-type]
            max_cost_usd=num("HIMERA_MAX_COST_USD", defaults.max_cost_usd),
            ocr_usd_per_page=num("HIMERA_OCR_USD_PER_PAGE", defaults.ocr_usd_per_page),
            timeout_s=num("HIMERA_TIMEOUT_S", defaults.timeout_s),
            max_retries=num("HIMERA_MAX_RETRIES", defaults.max_retries, int),
            backoff_base_s=num("HIMERA_BACKOFF_BASE_S", defaults.backoff_base_s),
            pages_per_chunk=num(
                "HIMERA_PAGES_PER_CHUNK", defaults.pages_per_chunk, int
            ),
            chunk_overlap_pages=num(
                "HIMERA_CHUNK_OVERLAP_PAGES", defaults.chunk_overlap_pages, int
            ),
            records_per_batch=num(
                "HIMERA_RECORDS_PER_BATCH", defaults.records_per_batch, int
            ),
            concurrency=num("HIMERA_CONCURRENCY", defaults.concurrency, int),
            provider_sort=env.get("HIMERA_PROVIDER_SORT", defaults.provider_sort),
            max_output_tokens=num(
                "HIMERA_MAX_OUTPUT_TOKENS", defaults.max_output_tokens, int
            ),
            max_repair_attempts=num(
                "HIMERA_MAX_REPAIR_ATTEMPTS", defaults.max_repair_attempts, int
            ),
        )
