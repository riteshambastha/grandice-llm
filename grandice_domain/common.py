from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)


METHODOLOGY_VERSION = "grandice-domain-1.0.0"
ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")

NonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
OpaqueId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
CategoryLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
]
CurrencyCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        pattern=r"^[A-Z]{3}$",
    ),
]


def _exact_decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(
            "Use a JSON string or integer for exact decimal values; floats are rejected."
        )
    if isinstance(value, Decimal):
        decimal_value = value
    elif isinstance(value, (str, int)):
        try:
            decimal_value = Decimal(value)
        except Exception as exc:
            raise ValueError("Value must be an exact decimal.") from exc
    else:
        raise ValueError("Value must be an exact decimal string, integer, or Decimal.")
    if not decimal_value.is_finite():
        raise ValueError("Decimal value must be finite.")
    _sign, digits, exponent = decimal_value.as_tuple()
    if len(digits) > 34 or decimal_value.adjusted() > 24 or exponent < -12:
        raise ValueError(
            "Decimal supports at most 34 digits, 12 decimal places, and magnitude below 1e25."
        )
    return decimal_value


def _non_negative(value: Decimal) -> Decimal:
    if value < ZERO:
        raise ValueError("Value must be non-negative.")
    return value


def _positive(value: Decimal) -> Decimal:
    if value <= ZERO:
        raise ValueError("Value must be positive.")
    return value


Money = Annotated[Decimal, BeforeValidator(_exact_decimal)]
NonNegativeMoney = Annotated[
    Decimal,
    BeforeValidator(_exact_decimal),
    AfterValidator(_non_negative),
]
PositiveMoney = Annotated[
    Decimal,
    BeforeValidator(_exact_decimal),
    AfterValidator(_positive),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceMetadata(StrictModel):
    source_id: OpaqueId
    citation: NonEmptyStr
    as_of: date


class AnalysisMetadata(StrictModel):
    as_of: date
    sources: tuple[SourceMetadata, ...] = Field(min_length=1, max_length=100)


class AnalysisDisclosures(StrictModel):
    methodology_version: str = METHODOLOGY_VERSION
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    warnings: tuple[str, ...]
    professional_review_required: Literal[True] = True


def divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == ZERO:
        raise ZeroDivisionError("denominator must not be zero")
    with localcontext() as context:
        context.prec = 34
        return numerator / denominator


def quantize(value: Decimal, places: str = "0.000001") -> Decimal:
    with localcontext() as context:
        context.prec = max(50, len(value.as_tuple().digits) + 16)
        return value.quantize(Decimal(places), rounding=ROUND_HALF_EVEN)
