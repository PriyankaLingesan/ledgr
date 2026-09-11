"""Shared API primitives: money, pagination, errors."""

from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.money import format_amount

T = TypeVar("T")

CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]


class Money(BaseModel):
    """A monetary value.

    `amount_minor` is canonical and integral; `amount` is the same value
    rendered in major units purely so responses are readable by humans.
    """

    amount_minor: int
    currency: str
    amount: str

    @classmethod
    def of(cls, amount_minor: int, currency: str) -> "Money":
        return cls(
            amount_minor=amount_minor,
            currency=currency,
            amount=format_amount(amount_minor, currency),
        )


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
