from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import (
    HUNDRED,
    AnalysisDisclosures,
    AnalysisMetadata,
    OpaqueId,
    SourceMetadata,
    StrictModel,
    ZERO,
    divide,
    quantize,
)


BoundedRating = int

_WEIGHTS: tuple[tuple[str, Decimal], ...] = (
    ("loss_tolerance", Decimal("0.30")),
    ("time_horizon", Decimal("0.25")),
    ("financial_stability", Decimal("0.20")),
    ("liquidity_flexibility", Decimal("0.15")),
    ("investment_knowledge", Decimal("0.10")),
)


class RiskProfile(StrictModel):
    source_id: OpaqueId
    loss_tolerance: BoundedRating = Field(strict=True, ge=1, le=5)
    time_horizon: BoundedRating = Field(strict=True, ge=1, le=5)
    financial_stability: BoundedRating = Field(strict=True, ge=1, le=5)
    liquidity_flexibility: BoundedRating = Field(strict=True, ge=1, le=5)
    investment_knowledge: BoundedRating = Field(strict=True, ge=1, le=5)


class RiskAssessmentInput(StrictModel):
    metadata: AnalysisMetadata
    profile: RiskProfile

    @model_validator(mode="after")
    def validate_metadata(self) -> "RiskAssessmentInput":
        source_ids = [source.source_id for source in self.metadata.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("metadata source_id values must be unique")
        if any(source.as_of > self.metadata.as_of for source in self.metadata.sources):
            raise ValueError("source as_of cannot be later than assessment as_of")
        if self.profile.source_id not in set(source_ids):
            raise ValueError("profile source_id must reference metadata.sources")
        return self


class RiskComponent(StrictModel):
    dimension: str
    rating: int
    minimum_rating: int
    maximum_rating: int
    weight: Decimal
    normalized_score: Decimal
    weighted_contribution: Decimal


class RiskScoreMetrics(StrictModel):
    score: Decimal
    score_minimum: Decimal
    score_maximum: Decimal
    components: tuple[RiskComponent, ...]


class RiskBand(StrictModel):
    code: Literal["lower", "moderate", "higher"]
    label: str
    lower_bound_inclusive: Decimal
    upper_bound_inclusive: Decimal


class RiskAssessment(AnalysisDisclosures):
    analysis_type: Literal["risk_assessment"] = "risk_assessment"
    as_of: date
    source_citations: tuple[SourceMetadata, ...]
    metrics: RiskScoreMetrics
    band: RiskBand


def calculate_risk_score(data: RiskAssessmentInput) -> RiskScoreMetrics:
    components: list[RiskComponent] = []
    total = ZERO
    for dimension, weight in _WEIGHTS:
        rating = getattr(data.profile, dimension)
        normalized = divide(Decimal(rating - 1), Decimal("4")) * HUNDRED
        contribution = normalized * weight
        total += contribution
        components.append(
            RiskComponent(
                dimension=dimension,
                rating=rating,
                minimum_rating=1,
                maximum_rating=5,
                weight=weight,
                normalized_score=quantize(normalized),
                weighted_contribution=quantize(contribution),
            )
        )
    return RiskScoreMetrics(
        score=quantize(total),
        score_minimum=ZERO,
        score_maximum=HUNDRED,
        components=tuple(components),
    )


def interpret_risk_score(metrics: RiskScoreMetrics) -> RiskBand:
    if metrics.score < Decimal("40"):
        return RiskBand(
            code="lower",
            label="Lower assessed risk capacity and tolerance",
            lower_bound_inclusive=ZERO,
            upper_bound_inclusive=Decimal("39.999999"),
        )
    if metrics.score < Decimal("70"):
        return RiskBand(
            code="moderate",
            label="Moderate assessed risk capacity and tolerance",
            lower_bound_inclusive=Decimal("40"),
            upper_bound_inclusive=Decimal("69.999999"),
        )
    return RiskBand(
        code="higher",
        label="Higher assessed risk capacity and tolerance",
        lower_bound_inclusive=Decimal("70"),
        upper_bound_inclusive=HUNDRED,
    )


def assess_client_risk(data: RiskAssessmentInput) -> RiskAssessment:
    metrics = calculate_risk_score(data)
    return RiskAssessment(
        as_of=data.metadata.as_of,
        source_citations=data.metadata.sources,
        metrics=metrics,
        band=interpret_risk_score(metrics),
        assumptions=(
            "Each dimension is caller-scored from 1 (lower) to 5 (higher).",
            "Weights are loss tolerance 30%, horizon 25%, stability 20%, liquidity flexibility 15%, knowledge 10%.",
            "Bands are lower [0,40), moderate [40,70), and higher [70,100].",
        ),
        limitations=(
            "This bounded questionnaire is not a psychometric instrument or suitability determination.",
            "The model excludes identity, age, account holdings, protected traits, and free-form personal data.",
            "Results depend on accurate caller scoring and can change as circumstances change.",
        ),
        warnings=(
            "The band must not be used by itself to make a product, allocation, buy, or sell recommendation.",
        ),
    )
