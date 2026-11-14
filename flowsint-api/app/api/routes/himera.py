"""Himera Search PDF import (LLM extraction via OpenRouter) into a sketch."""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from flowsint_core.core.graph import create_graph_service
from flowsint_core.core.models import HimeraImport, Profile
from flowsint_core.core.postgre_db import SessionLocal, get_db
from flowsint_core.core.services import (
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from flowsint_core.core.services.himera_import_service import (
    MAX_PDF_BYTES,
    create_himera_import_service,
)
from flowsint_core.core.services.type_registry_service import (
    create_type_registry_service,
)

router = APIRouter()

# Progress of the background analysis (ranges, tokens, cost, retries) is logged at
# INFO by flowsint_core.himera; uvicorn only wires its own loggers, so attach a
# handler here. Messages carry no PDF content (see openrouter.py).
_himera_logger = logging.getLogger("flowsint_core.himera")
if not _himera_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    _himera_logger.addHandler(_handler)
    _himera_logger.setLevel(logging.INFO)
    _himera_logger.propagate = False


class HimeraImportRead(BaseModel):
    id: UUID
    sketch_id: UUID
    filename: Optional[str] = None
    status: str
    error: Optional[str] = None
    model: Optional[str] = None
    pdf_engine: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    import_summary: Optional[Dict[str, Any]] = None
    progress: Optional[Dict[str, Any]] = None
    reused: bool = False


class HimeraExecuteInput(BaseModel):
    entity_ids: Optional[List[str]] = None
    relationship_ids: Optional[List[str]] = None
    include_source_records: bool = True


def _read(
    row: HimeraImport, reused: bool = False, with_result: bool = True
) -> HimeraImportRead:
    return HimeraImportRead(
        id=row.id,
        sketch_id=row.sketch_id,
        filename=row.filename,
        status=row.status,
        error=row.error,
        model=row.model,
        pdf_engine=row.pdf_engine,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        cost_usd=row.cost_usd,
        result=row.result if with_result else None,
        import_summary=row.import_summary,
        progress=row.progress,
        reused=reused,
    )


def _parse_uuid(value: str, what: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"{what} not found")


async def _process_in_background(import_id: UUID, pdf_bytes: bytes) -> None:
    db = SessionLocal()
    try:
        await create_himera_import_service(db).process(import_id, pdf_bytes)
    finally:
        db.close()


@router.post("/{sketch_id}/import/himera", response_model=HimeraImportRead)
async def start_himera_import(
    sketch_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    force: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Upload a Himera Search PDF and start the OpenRouter analysis."""
    sid = _parse_uuid(sketch_id, "Sketch")
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are supported")
    content = await file.read(MAX_PDF_BYTES + 1)
    service = create_himera_import_service(db)
    try:
        row, needs_processing = service.start(
            sid, current_user.id, file.filename, content, force=force
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Sketch not found")
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Forbidden")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if needs_processing:
        background_tasks.add_task(_process_in_background, row.id, content)
    return _read(row, reused=not needs_processing, with_result=False)


@router.get("/{sketch_id}/import/himera/balance")
async def get_openrouter_balance(
    sketch_id: str,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """OpenRouter credits left for the key used by this user's Himera imports."""
    from flowsint_core.himera.openrouter import OpenRouterError

    service = create_himera_import_service(db)
    try:
        return await service.balance(_parse_uuid(sketch_id, "Sketch"), current_user.id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Forbidden")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenRouterError as e:
        raise HTTPException(status_code=502, detail=f"OpenRouter: {str(e)[:200]}")


@router.get("/{sketch_id}/import/himera/{import_id}", response_model=HimeraImportRead)
def get_himera_import(
    sketch_id: str,
    import_id: str,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Status and, once completed, the extraction result for the preview."""
    service = create_himera_import_service(db)
    try:
        row = service.get(
            _parse_uuid(sketch_id, "Sketch"),
            _parse_uuid(import_id, "Import"),
            current_user.id,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Forbidden")
    return _read(row)


@router.post("/{sketch_id}/import/himera/{import_id}/execute")
def execute_himera_import(
    sketch_id: str,
    import_id: str,
    payload: HimeraExecuteInput,
    db: Session = Depends(get_db),
    current_user: Profile = Depends(get_current_user),
):
    """Write the entities/relationships confirmed in the preview into the graph."""
    sid = _parse_uuid(sketch_id, "Sketch")
    service = create_himera_import_service(db)
    resolver = create_type_registry_service(db).build_type_resolver(current_user.id)
    graph_service = create_graph_service(
        sketch_id=str(sid), enable_batching=False, type_resolver=resolver
    )
    try:
        summary = service.execute(
            sid,
            _parse_uuid(import_id, "Import"),
            current_user.id,
            graph_service,
            entity_ids=payload.entity_ids,
            relationship_ids=payload.relationship_ids,
            include_source_records=payload.include_source_records,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionDeniedError:
        raise HTTPException(status_code=403, detail="Forbidden")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    status = "completed_with_errors" if summary["errors"] else "completed"
    return {"status": status, **summary}
