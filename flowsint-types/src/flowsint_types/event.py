from typing import Optional, Self

from pydantic import Field, model_validator

from .flowsint_base import FlowsintType
from .registry import flowsint_type


@flowsint_type
class Event(FlowsintType):
    """A dated fact or a claim made by a source (label, score, registration, departure ...).

    ``assertion`` tells whether the event is a statement of a source
    ("source_claim") rather than a verified fact.
    """

    title: str = Field(
        ...,
        description="Short description of the event or claim",
        title="Title",
        json_schema_extra={"primary": True},
    )
    date: Optional[str] = Field(None, description="Date or period", title="Date")
    description: Optional[str] = Field(
        None, description="Details as stated by the source", title="Description"
    )
    assertion: Optional[str] = Field(
        None,
        description="fact | source_claim",
        title="Assertion",
    )
    source: Optional[str] = Field(
        None, description="Source that states the event", title="Source"
    )

    @model_validator(mode="after")
    def compute_label(self) -> Self:
        self.nodeLabel = f"{self.title} ({self.date})" if self.date else self.title
        return self

    @classmethod
    def from_string(cls, line: str):
        return cls(title=line.strip())

    @classmethod
    def detect(cls, line: str) -> bool:
        return False
