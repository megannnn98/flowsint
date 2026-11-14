"""
Data models of the Himera import.

Two families:

* ``Llm*`` models mirror exactly what the model must return (strict JSON
  Schema in ``RECORDS_SCHEMA`` / ``ENTITIES_SCHEMA``). Pages there are relative
  to the PDF part that was sent.
* Result models (``SourceRecord``, ``ExtractedEntity`` ...) are produced by the
  deterministic code after the LLM and form ``HimeraExtractionResult``. They do
  not depend on Neo4j.
"""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Section(str, Enum):
    SUMMARY = "summary"  # УСТАНОВОЧНЫЕ ДАННЫЕ
    SCORING = "scoring"  # Результаты скоринга
    LABELS = "labels"  # Метки
    INCOME = "income"  # Доходы
    ADDRESSES = "addresses"  # Адреса
    DATASET_RECORD = "dataset_record"  # detailed record of one dataset
    SERVICE = "service"  # footer / service info


class EntityType(str, Enum):
    INDIVIDUAL = "Individual"
    PHONE = "Phone"
    EMAIL = "Email"
    USERNAME = "Username"
    SOCIAL_ACCOUNT = "SocialAccount"
    ORGANIZATION = "Organization"
    ADDRESS = "Address"
    DOCUMENT = "Document"
    IDENTIFIER = "Identifier"
    EVENT = "Event"


class SubjectLink(str, Enum):
    SUBJECT = "subject"  # the person the report is about
    OTHER_PERSON = "other_person"  # namesake / relative / someone else
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class RelationshipStatus(str, Enum):
    CONFIRMED = "confirmed"  # the record states the link directly
    CANDIDATE = "candidate"  # possible, identity not established
    SOURCE_CLAIM = "source_claim"  # assessment/label claimed by the source


class Verification(str, Enum):
    VALIDATED = "validated"
    UNVERIFIED = "unverified"


class FieldValue(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    value: str


# ---------------------------------------------------------------- LLM pass 1


class LlmRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")
    section: Section
    source_name: Optional[str] = None
    source_heading: str
    source_period: Optional[str] = None
    page_start: int
    page_end: int
    ordinal_on_start_page: int = Field(ge=1)
    starts_before_range: bool = False
    continues_after_range: bool = False
    fields: List[FieldValue] = Field(default_factory=list)
    raw_text: str = ""
    name_match_only: bool = False


class LlmRecordsOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    report_id: Optional[str] = None
    truncation_notice: bool = False
    records: List[LlmRecord]


# ---------------------------------------------------------------- LLM pass 2


class LlmEntity(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entity_id: str
    type: EntityType
    raw_value: str
    normalized_value: Optional[str] = None
    record_id: str
    page: int
    evidence: str
    confidence: float = Field(ge=0, le=1)
    subject_link: SubjectLink = SubjectLink.NOT_APPLICABLE
    role: Optional[str] = None
    attributes: List[FieldValue] = Field(default_factory=list)


class LlmRelationship(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    source_entity_id: str
    target_entity_id: str
    record_id: str
    evidence: str
    confidence: float = Field(ge=0, le=1)
    status: RelationshipStatus


class LlmEntitiesOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    entities: List[LlmEntity]
    relationships: List[LlmRelationship]


# ------------------------------------------------------------------ results


class SourceRecord(BaseModel):
    record_id: str
    section: Section
    source_name: Optional[str] = None
    source_heading: str
    source_period: Optional[str] = None
    page_start: int
    page_end: int
    ordinal_on_start_page: int
    fields: List[FieldValue] = Field(default_factory=list)
    raw_text: str = ""
    name_match_only: bool = False
    # True while the end of the record has not been seen (last page of a range).
    open_ended: bool = False
    chunks: List[str] = Field(default_factory=list)


class Mention(BaseModel):
    record_id: str
    page: int
    raw_value: str
    evidence: str
    confidence: float


class ExtractedEntity(BaseModel):
    entity_id: str
    type: EntityType
    raw_value: str
    normalized_value: str
    confidence: float
    subject_link: SubjectLink = SubjectLink.NOT_APPLICABLE
    role: Optional[str] = None
    attributes: Dict[str, str] = Field(default_factory=dict)
    mentions: List[Mention]
    verification: Verification = Verification.VALIDATED
    issues: List[str] = Field(default_factory=list)

    @property
    def record_ids(self) -> List[str]:
        return sorted({m.record_id for m in self.mentions})

    @property
    def pages(self) -> List[int]:
        return sorted({m.page for m in self.mentions})


class ExtractedRelationship(BaseModel):
    rel_id: str
    type: str
    source_entity_id: str
    target_entity_id: str
    record_id: str
    page_start: int
    page_end: int
    evidence: str
    confidence: float
    status: RelationshipStatus
    issues: List[str] = Field(default_factory=list)


class RejectedItem(BaseModel):
    kind: Literal["record", "entity", "relationship"]
    reason: str
    data: Dict[str, Any]


class UsageSummary(BaseModel):
    model: str
    pdf_engine: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class HimeraExtractionResult(BaseModel):
    report_id: Optional[str]
    pdf_sha256: str
    page_count: int
    records: List[SourceRecord]
    entities: List[ExtractedEntity] = Field(default_factory=list)
    unverified_entities: List[ExtractedEntity] = Field(default_factory=list)
    relationships: List[ExtractedRelationship] = Field(default_factory=list)
    ambiguous_relationships: List[ExtractedRelationship] = Field(default_factory=list)
    rejected: List[RejectedItem] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    truncation_notice: bool = False
    complete: bool = True
    usage: UsageSummary


# ------------------------------------------------------- strict JSON Schemas


def _s() -> Dict[str, Any]:
    return {"type": "string"}


def _ns() -> Dict[str, Any]:
    return {"type": ["string", "null"]}


def _obj(props: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


_FIELDS = {"type": "array", "items": _obj({"name": _s(), "value": _s()})}

RECORDS_SCHEMA: Dict[str, Any] = _obj(
    {
        "report_id": _ns(),
        "truncation_notice": {"type": "boolean"},
        "records": {
            "type": "array",
            "items": _obj(
                {
                    "section": {"type": "string", "enum": [s.value for s in Section]},
                    "source_name": _ns(),
                    "source_heading": _s(),
                    "source_period": _ns(),
                    "page_start": {"type": "integer"},
                    "page_end": {"type": "integer"},
                    "ordinal_on_start_page": {"type": "integer"},
                    "starts_before_range": {"type": "boolean"},
                    "continues_after_range": {"type": "boolean"},
                    "fields": _FIELDS,
                    "name_match_only": {"type": "boolean"},
                }
            ),
        },
    }
)

ENTITIES_SCHEMA: Dict[str, Any] = _obj(
    {
        "entities": {
            "type": "array",
            "items": _obj(
                {
                    "entity_id": _s(),
                    "type": {"type": "string", "enum": [t.value for t in EntityType]},
                    "raw_value": _s(),
                    "normalized_value": _ns(),
                    "record_id": _s(),
                    "page": {"type": "integer"},
                    "evidence": _s(),
                    "confidence": {"type": "number"},
                    "subject_link": {
                        "type": "string",
                        "enum": [s.value for s in SubjectLink],
                    },
                    "role": _ns(),
                    "attributes": _FIELDS,
                }
            ),
        },
        "relationships": {
            "type": "array",
            "items": _obj(
                {
                    "type": _s(),
                    "source_entity_id": _s(),
                    "target_entity_id": _s(),
                    "record_id": _s(),
                    "evidence": _s(),
                    "confidence": {"type": "number"},
                    "status": {
                        "type": "string",
                        "enum": [s.value for s in RelationshipStatus],
                    },
                }
            ),
        },
    }
)
