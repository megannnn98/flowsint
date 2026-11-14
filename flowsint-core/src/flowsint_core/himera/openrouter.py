"""
Minimal OpenRouter Chat Completions client for structured PDF extraction.

Why not ``core.llm.OpenAIProvider``: that provider only does text chat. The
Himera import needs a ``file`` content part, the ``file-parser`` plugin (PDF
engine), ``response_format: json_schema``, ``finish_reason`` and ``usage.cost``.
This client reuses the same base URL and key lookup and adds only that.

Logging rule: never log request/response bodies (they carry personal data).
Only model, status, token counts, cost and error classes are logged.
"""

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

import httpx

from flowsint_core.core.llm.factory import OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

StructuredMode = Literal["json_schema", "json_object", "prompt"]

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_ERROR_TEXT_LIMIT = 300


class OpenRouterError(Exception):
    """Base error of the OpenRouter client."""


class OpenRouterHTTPError(OpenRouterError):
    def __init__(self, status: int, message: str):
        super().__init__(f"OpenRouter HTTP {status}: {message}")
        self.status = status


class OpenRouterRetryExhausted(OpenRouterError):
    pass


class ModelNotFoundError(OpenRouterError):
    pass


class ModelCapabilityError(OpenRouterError):
    pass


class BudgetExceededError(OpenRouterError):
    pass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    context_length: int
    prompt_price: float  # USD per token
    completion_price: float  # USD per token
    input_modalities: List[str]
    supported_parameters: List[str]

    @property
    def accepts_files(self) -> bool:
        return "file" in self.input_modalities

    @property
    def structured_mode(self) -> StructuredMode:
        if "structured_outputs" in self.supported_parameters:
            return "json_schema"
        if "response_format" in self.supported_parameters:
            return "json_object"
        return "prompt"

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "ModelInfo":
        pricing = data.get("pricing") or {}
        arch = data.get("architecture") or {}
        return cls(
            id=data["id"],
            context_length=int(data.get("context_length") or 0),
            prompt_price=float(pricing.get("prompt") or 0),
            completion_price=float(pricing.get("completion") or 0),
            input_modalities=list(arch.get("input_modalities") or []),
            supported_parameters=list(data.get("supported_parameters") or []),
        )


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost_usd += other.cost_usd
        self.calls += other.calls


@dataclass(frozen=True)
class Balance:
    remaining_usd: Optional[float]  # None = unlimited key without account access
    total_usage: float = 0.0
    total_credits: Optional[float] = None
    key_limit: Optional[float] = None
    source: str = "credits"  # "credits" (account) or "key" (this key's limit)


@dataclass
class ChatResult:
    content: str
    finish_reason: Optional[str]
    usage: Usage
    model: str


@dataclass
class CostBudget:
    """Cumulative USD cap for one document.

    Concurrent calls reserve their worst-case estimate before sending (``reserve``)
    and settle it with the actual cost afterwards (``settle``), so parallel calls
    cannot jointly overshoot the cap. reserve/settle contain no ``await``: they
    are atomic on the event loop.
    """

    max_usd: float
    spent_usd: float = 0.0
    reserved_usd: float = 0.0
    calls: List[float] = field(default_factory=list)

    def check(self, estimate_usd: float) -> None:
        if self.spent_usd + self.reserved_usd + estimate_usd > self.max_usd:
            raise BudgetExceededError(
                f"Processing budget exceeded: spent ${self.spent_usd:.4f}, "
                f"in flight ${self.reserved_usd:.4f}, next call estimated "
                f"${estimate_usd:.4f}, limit ${self.max_usd:.4f}"
            )

    def reserve(self, estimate_usd: float) -> None:
        self.check(estimate_usd)
        self.reserved_usd += estimate_usd

    def release(self, estimate_usd: float) -> None:
        self.reserved_usd = max(0.0, self.reserved_usd - estimate_usd)

    def settle(self, estimate_usd: float, cost_usd: float) -> None:
        self.release(estimate_usd)
        self.charge(cost_usd)

    def charge(self, cost_usd: float) -> None:
        self.spent_usd += cost_usd
        self.calls.append(cost_usd)


def pdf_file_part(pdf_bytes: bytes, filename: str) -> Dict[str, Any]:
    encoded = base64.b64encode(pdf_bytes).decode("ascii")
    return {
        "type": "file",
        "file": {
            "filename": filename,
            "file_data": f"data:application/pdf;base64,{encoded}",
        },
    }


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        timeout_s: float = 180.0,
        max_retries: int = 3,
        backoff_base_s: float = 2.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._max_retries = max_retries
        self._backoff = backoff_base_s
        self._transport = transport
        self._sleep = sleep
        self._models_cache: Optional[Dict[str, ModelInfo]] = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            transport=self._transport,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "X-Title": "Flowsint",
            },
        )

    async def _request(
        self, method: str, path: str, json: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                delay = self._backoff * (2 ** (attempt - 1))
                retry_after = getattr(last_error, "retry_after", None)
                if retry_after:
                    delay = max(delay, retry_after)
                logger.warning(
                    "openrouter retry %s/%s in %.1fs after %s",
                    attempt,
                    self._max_retries,
                    delay,
                    type(last_error).__name__,
                )
                await self._sleep(delay)
            try:
                async with self._client() as client:
                    response = await client.request(method, path, json=json)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = e
                continue

            if response.status_code in _RETRYABLE_STATUS:
                err = OpenRouterHTTPError(response.status_code, _error_text(response))
                err.retry_after = _retry_after(response)  # type: ignore[attr-defined]
                last_error = err
                continue
            if response.status_code >= 400:
                raise OpenRouterHTTPError(response.status_code, _error_text(response))
            try:
                body = response.json()
            except ValueError as e:
                raise OpenRouterError("OpenRouter returned a non-JSON body") from e
            # OpenRouter can report upstream errors with HTTP 200.
            if isinstance(body, dict) and body.get("error"):
                error = body["error"]
                code = error.get("code") if isinstance(error, dict) else None
                message = (
                    error.get("message") if isinstance(error, dict) else str(error)
                )
                if isinstance(code, int) and code in _RETRYABLE_STATUS:
                    last_error = OpenRouterHTTPError(
                        code, str(message)[:_ERROR_TEXT_LIMIT]
                    )
                    continue
                raise OpenRouterHTTPError(
                    code if isinstance(code, int) else 502,
                    str(message)[:_ERROR_TEXT_LIMIT],
                )
            return body
        raise OpenRouterRetryExhausted(
            f"OpenRouter request failed after {self._max_retries + 1} attempts: "
            f"{type(last_error).__name__}: {str(last_error)[:_ERROR_TEXT_LIMIT]}"
        )

    async def get_balance(self) -> "Balance":
        """Account credits left (GET /credits); falls back to the key's own
        remaining limit (GET /key) when the key may not read account credits."""
        try:
            data = (await self._request("GET", "/credits")).get("data") or {}
            total = float(data.get("total_credits") or 0)
            used = float(data.get("total_usage") or 0)
            return Balance(
                remaining_usd=total - used, total_credits=total, total_usage=used
            )
        except OpenRouterHTTPError as e:
            if e.status not in (401, 403, 404):
                raise
        data = (await self._request("GET", "/key")).get("data") or {}
        remaining = data.get("limit_remaining")
        return Balance(
            remaining_usd=float(remaining) if remaining is not None else None,
            total_usage=float(data.get("usage") or 0),
            key_limit=float(data["limit"]) if data.get("limit") is not None else None,
            source="key",
        )

    async def get_model(self, model_id: str) -> ModelInfo:
        if self._models_cache is None:
            body = await self._request("GET", "/models")
            self._models_cache = {
                m["id"]: ModelInfo.from_api(m) for m in body.get("data", [])
            }
        info = self._models_cache.get(model_id)
        if info is None:
            raise ModelNotFoundError(
                f"Model '{model_id}' is not available on OpenRouter"
            )
        return info

    async def chat_json(
        self,
        *,
        model: ModelInfo,
        messages: List[Dict[str, Any]],
        schema_name: str,
        schema: Dict[str, Any],
        max_tokens: int,
        budget: CostBudget,
        estimated_prompt_tokens: int,
        pdf_engine: Optional[str] = None,
        extra_cost_estimate_usd: float = 0.0,
        provider_sort: Optional[str] = None,
    ) -> ChatResult:
        estimate = (
            estimated_prompt_tokens * model.prompt_price
            + max_tokens * model.completion_price
            + extra_cost_estimate_usd
        )
        budget.reserve(estimate)
        try:
            return await self._chat_json(
                model,
                messages,
                schema_name,
                schema,
                max_tokens,
                pdf_engine,
                budget,
                estimate,
                provider_sort,
            )
        except BaseException:
            # Failed before settle: free the reservation (the call is not charged
            # locally; OpenRouter does not bill failed requests).
            budget.release(estimate)
            raise

    async def _chat_json(
        self,
        model: ModelInfo,
        messages: List[Dict[str, Any]],
        schema_name: str,
        schema: Dict[str, Any],
        max_tokens: int,
        pdf_engine: Optional[str],
        budget: CostBudget,
        estimate: float,
        provider_sort: Optional[str] = None,
    ) -> ChatResult:
        payload: Dict[str, Any] = {
            "model": model.id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "usage": {"include": True},
        }
        mode = model.structured_mode
        if mode == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        elif mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if provider_sort:
            payload["provider"] = {"sort": provider_sort}
        if pdf_engine:
            payload["plugins"] = [{"id": "file-parser", "pdf": {"engine": pdf_engine}}]

        body = await self._request("POST", "/chat/completions", json=payload)
        choices = body.get("choices") or []
        if not choices:
            raise OpenRouterError("OpenRouter response has no choices")
        choice = choices[0]
        content = (choice.get("message") or {}).get("content") or ""
        raw_usage = body.get("usage") or {}
        prompt_tokens = int(raw_usage.get("prompt_tokens") or 0)
        completion_tokens = int(raw_usage.get("completion_tokens") or 0)
        cost = raw_usage.get("cost")
        if cost is None:
            cost = (
                prompt_tokens * model.prompt_price
                + completion_tokens * model.completion_price
            )
        usage = Usage(prompt_tokens, completion_tokens, float(cost), 1)
        budget.settle(estimate, usage.cost_usd)
        logger.info(
            "openrouter call model=%s schema=%s finish=%s prompt_tokens=%s "
            "completion_tokens=%s cost=%.5f",
            body.get("model") or model.id,
            schema_name,
            choice.get("finish_reason"),
            prompt_tokens,
            completion_tokens,
            usage.cost_usd,
        )
        return ChatResult(
            content=content if isinstance(content, str) else str(content),
            finish_reason=choice.get("finish_reason"),
            usage=usage,
            model=body.get("model") or model.id,
        )


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:_ERROR_TEXT_LIMIT]
    except ValueError:
        pass
    return response.reason_phrase or "error"


def _retry_after(response: httpx.Response) -> Optional[float]:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None
