from typing import Optional, Self

from pydantic import Field, model_validator

from .flowsint_base import FlowsintType
from .registry import flowsint_type


@flowsint_type
class Identifier(FlowsintType):
    """A registry/tax/document identifier (ИНН, СНИЛС, ОГРН, ИИН, card/account number ...)."""

    value: str = Field(
        ...,
        description="Identifier value (digits only when the scheme is numeric)",
        title="Value",
        json_schema_extra={"primary": True},
    )
    scheme: Optional[str] = Field(
        None,
        description="Identifier scheme: inn, snils, ogrn, iin, bank_card, bank_account, other",
        title="Scheme",
    )
    country: Optional[str] = Field(
        None, description="Issuing country (ISO 3166-1 alpha-2)", title="Country"
    )

    @model_validator(mode="after")
    def compute_label(self) -> Self:
        prefix = f"{self.scheme.upper()} " if self.scheme else ""
        self.nodeLabel = f"{prefix}{self.value}"
        return self

    @classmethod
    def from_string(cls, line: str):
        return cls(value=line.strip())

    @classmethod
    def detect(cls, line: str) -> bool:
        return False
