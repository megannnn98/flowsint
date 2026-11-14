"""
Himera PDF import: analysis jobs per sketch and the confirmed graph import.

Flow: ``start`` (store a row, reuse a finished analysis of the same PDF) →
``process`` (OpenRouter extraction, in the background) → ``get`` (preview) →
``execute`` (write the selected entities/relationships into Neo4j).
"""

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import HimeraImport, Sketch
from ..repositories import InvestigationRepository, SketchRepository
from .base import BaseService
from .exceptions import NotFoundError, ValidationError
from .vault_service import VaultService

logger = logging.getLogger("flowsint_core.himera.service")

OPENROUTER_KEY_NAME = "OPENROUTER_API_KEY"
MAX_PDF_BYTES = 20 * 1024 * 1024
# A "processing" row older than this lost its background task (e.g. API restart).
STALE_PROCESSING = timedelta(minutes=30)


def _is_stale(row: HimeraImport) -> bool:
    if row.status != "processing" or row.created_at is None:
        return False
    created = row.created_at
    if created.tzinfo is None:  # SQLite returns naive UTC timestamps
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > STALE_PROCESSING


class HimeraImportService(BaseService):
    def __init__(
        self,
        db: Session,
        sketch_repo: SketchRepository,
        investigation_repo: InvestigationRepository,
        vault_service: VaultService,
        **kwargs,
    ):
        super().__init__(db, **kwargs)
        self._sketch_repo = sketch_repo
        self._investigation_repo = investigation_repo
        self._vault = vault_service

    # ------------------------------------------------------------ access

    def _sketch(self, sketch_id: UUID, user_id: UUID, action: str) -> Sketch:
        sketch = self._sketch_repo.get_by_id(sketch_id)
        if not sketch:
            raise NotFoundError("Sketch not found")
        self._check_permission(user_id, sketch.investigation_id, [action])
        return sketch

    def get(self, sketch_id: UUID, import_id: UUID, user_id: UUID) -> HimeraImport:
        self._sketch(sketch_id, user_id, "read")
        row = self._db.get(HimeraImport, import_id)
        if row is None or row.sketch_id != sketch_id:
            raise NotFoundError("Import not found")
        return row

    # ----------------------------------------------------------- start

    def start(
        self,
        sketch_id: UUID,
        user_id: UUID,
        filename: str,
        pdf_bytes: bytes,
        force: bool = False,
    ) -> tuple[HimeraImport, bool]:
        """Return (row, needs_processing)."""
        self._sketch(sketch_id, user_id, "update")
        if not pdf_bytes.startswith(b"%PDF"):
            raise ValidationError("File is not a PDF")
        if len(pdf_bytes) > MAX_PDF_BYTES:
            raise ValidationError("PDF is larger than 20 MB")
        sha = hashlib.sha256(pdf_bytes).hexdigest()
        if not force:
            existing = (
                self._db.query(HimeraImport)
                .filter(
                    HimeraImport.sketch_id == sketch_id,
                    HimeraImport.pdf_sha256 == sha,
                    HimeraImport.status.in_(["completed", "processing"]),
                )
                .order_by(HimeraImport.created_at.desc())
                .first()
            )
            if existing is not None and not _is_stale(existing):
                return existing, False
        row = HimeraImport(
            sketch_id=sketch_id,
            owner_id=user_id,
            filename=filename[:255],
            pdf_sha256=sha,
            status="processing",
        )
        self._db.add(row)
        self._commit()
        self._refresh(row)
        return row, True

    async def balance(
        self,
        sketch_id: UUID,
        user_id: UUID,
        client_factory: Optional[Callable[[str], object]] = None,
    ) -> dict:
        """OpenRouter credits left for the key this user's imports would use."""
        from flowsint_core.himera.openrouter import OpenRouterClient

        self._sketch(sketch_id, user_id, "read")
        key = self.resolve_api_key(user_id)
        client = (
            client_factory(key)
            if client_factory
            else OpenRouterClient(key, timeout_s=15, max_retries=1)
        )
        b = await client.get_balance()  # type: ignore[attr-defined]
        return {
            "remaining_usd": b.remaining_usd,
            "total_usage_usd": b.total_usage,
            "total_credits_usd": b.total_credits,
            "key_limit_usd": b.key_limit,
            "source": b.source,
        }

    # --------------------------------------------------------- process

    def resolve_api_key(self, owner_id: UUID) -> str:
        key = self._vault.get_secret(owner_id, OPENROUTER_KEY_NAME) or os.environ.get(
            OPENROUTER_KEY_NAME
        )
        if not key:
            raise ValidationError(
                "OPENROUTER_API_KEY is not configured (add it to the Vault or the environment)"
            )
        return key

    async def process(
        self,
        import_id: UUID,
        pdf_bytes: bytes,
        client_factory: Optional[Callable[[str], object]] = None,
    ) -> HimeraImport:
        from flowsint_core.himera.config import HimeraSettings
        from flowsint_core.himera.extraction import HimeraExtractionService
        from flowsint_core.himera.openrouter import OpenRouterClient

        row = self._db.get(HimeraImport, import_id)
        if row is None:
            raise NotFoundError("Import not found")
        try:
            settings = HimeraSettings.from_env()
            api_key = self.resolve_api_key(row.owner_id)
            client = (
                client_factory(api_key)
                if client_factory
                else OpenRouterClient(
                    api_key,
                    timeout_s=settings.timeout_s,
                    max_retries=settings.max_retries,
                    backoff_base_s=settings.backoff_base_s,
                )
            )
            result = await HimeraExtractionService(client, settings).extract(  # type: ignore[arg-type]
                pdf_bytes, filename="report.pdf"
            )
        except Exception as e:  # noqa: BLE001 - stored for the user, never re-raised
            # Messages come from our own code / OpenRouter errors; no PDF content.
            row.status = "failed"
            row.error = f"{type(e).__name__}: {str(e)[:500]}"
            logger.warning("himera import %s failed: %s", import_id, type(e).__name__)
            self._commit()
            return row

        row.status = "completed"
        row.error = None
        row.result = result.model_dump(mode="json")
        row.model = result.usage.model
        row.pdf_engine = result.usage.pdf_engine
        row.prompt_tokens = result.usage.prompt_tokens
        row.completion_tokens = result.usage.completion_tokens
        row.cost_usd = result.usage.cost_usd
        self._commit()
        logger.info(
            "himera import %s completed: records=%s entities=%s unverified=%s cost=%.4f",
            import_id,
            len(result.records),
            len(result.entities),
            len(result.unverified_entities),
            result.usage.cost_usd,
        )
        return row

    # --------------------------------------------------------- execute

    def execute(
        self,
        sketch_id: UUID,
        import_id: UUID,
        user_id: UUID,
        graph_service,
        entity_ids: Optional[list[str]] = None,
        relationship_ids: Optional[list[str]] = None,
        include_source_records: bool = True,
    ) -> dict:
        from flowsint_core.himera.importer import HimeraGraphImporter, ImportSelection
        from flowsint_core.himera.schemas import HimeraExtractionResult

        self._sketch(sketch_id, user_id, "update")
        row = self.get(sketch_id, import_id, user_id)
        if row.status != "completed" or not row.result:
            raise ValidationError("Analysis is not completed")
        result = HimeraExtractionResult.model_validate(row.result)
        selection = ImportSelection(
            entity_ids=set(entity_ids) if entity_ids is not None else None,
            relationship_ids=set(relationship_ids)
            if relationship_ids is not None
            else None,
            include_source_records=include_source_records,
        )
        summary = HimeraGraphImporter(graph_service).import_result(result, selection)
        data = {
            "nodes_written": summary.nodes_written,
            "edges_written": summary.edges_written,
            "skipped_relationships": summary.skipped_relationships,
            "errors": summary.errors[:50],
        }
        row.import_summary = data
        row.imported_at = datetime.now(timezone.utc)
        self._commit()
        return data


def create_himera_import_service(db: Session) -> HimeraImportService:
    return HimeraImportService(
        db=db,
        sketch_repo=SketchRepository(db),
        investigation_repo=InvestigationRepository(db),
        vault_service=VaultService(db=db),
    )
