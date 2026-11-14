from typing import List, Optional, Self

from pydantic import Field, model_validator

from .flowsint_base import FlowsintType
from .registry import flowsint_type


@flowsint_type
class SourceRecord(FlowsintType):
    """One record of one dataset inside an imported report (provenance node)."""

    record_key: str = Field(
        ...,
        description="Stable key: <report id>/<record id>",
        title="Record key",
        json_schema_extra={"primary": True},
    )
    report_id: Optional[str] = Field(
        None, description="Report identifier", title="Report"
    )
    record_id: Optional[str] = Field(
        None, description="Record id inside the report", title="Record ID"
    )
    source_name: Optional[str] = Field(None, description="Dataset name", title="Source")
    source_heading: Optional[str] = Field(
        None, description="Original heading of the record", title="Heading"
    )
    source_period: Optional[str] = Field(
        None, description="Dataset date or period", title="Period"
    )
    section: Optional[str] = Field(None, description="Report section", title="Section")
    page_start: Optional[int] = Field(None, description="First page", title="Page from")
    page_end: Optional[int] = Field(None, description="Last page", title="Page to")
    fields: Optional[List[str]] = Field(
        None, description="'name: value' pairs as printed", title="Fields"
    )
    name_match_only: Optional[bool] = Field(
        None, description="Record matched by full name only", title="Name match only"
    )

    @model_validator(mode="after")
    def compute_label(self) -> Self:
        pages = ""
        if self.page_start:
            pages = (
                f" (p. {self.page_start})"
                if self.page_end in (None, self.page_start)
                else f" (p. {self.page_start}-{self.page_end})"
            )
        self.nodeLabel = f"{self.source_heading or self.record_key}{pages}"
        return self

    @classmethod
    def detect(cls, line: str) -> bool:
        return False
