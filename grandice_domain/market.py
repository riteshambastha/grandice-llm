from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, localcontext
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BeforeValidator,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .common import (
    AnalysisDisclosures,
    AnalysisMetadata,
    OpaqueId,
    SourceMetadata,
    StrictModel,
    _exact_decimal,
    divide,
    quantize,
)


MARKET_METHODOLOGY_VERSION = "grandice-market-deterministic-1.3.0"
MAX_EVIDENCE_ITEMS = 2_000
MAX_OUTPUT_ROWS = 300
MAX_EXCERPT_CHARS = 160
MAX_STATEMENT_CHARS = 8_000
MAX_TITLE_CHARS = 300
MAX_TAGS = 20
MAX_SCOPE_SUBJECTS = 100
MAX_SCOPE_METRICS = 100
MAX_ENTITIES = 25
MAX_SOURCES = 100
MAX_BINDINGS_PER_OUTPUT = 8
MAX_INCOMPARABILITY_REASONS = 12
MAX_LATEST_METRICS = 75
MAX_TRENDS = 100
MAX_CONTRADICTIONS = 60
MAX_GAPS = 65
MAX_PROFILE_METRICS = 4
MAX_PROFILE_BINDINGS = 2
MAX_COMPARISON_BINDINGS = 1
MAX_COMPETITIVE_TOTAL_BINDINGS = 250
DECIMAL_WORK_PRECISION = 80
EVIDENCE_CANONICALIZATION_VERSION = "grandice-market-evidence-canonical-v1"
ANALYSIS_MANIFEST_VERSION = "grandice-market-analysis-manifest-v1"

BoundedTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_CHARS)
]
BoundedStatement = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_STATEMENT_CHARS)
]
BoundedLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
Tag = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)
]
ExactDecimal = Annotated[Decimal, BeforeValidator(_exact_decimal)]


def _exact_derived_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("Derived decimal values reject floats.")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (str, int)):
        result = Decimal(value)
    else:
        raise ValueError("Derived value must be an exact decimal.")
    if not result.is_finite() or len(result.as_tuple().digits) > 70:
        raise ValueError("Derived decimal must be finite with at most 70 digits.")
    return result


DerivedDecimal = Annotated[Decimal, BeforeValidator(_exact_derived_decimal)]
CurrencyCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_upper=True,
        pattern=r"^[A-Z]{3}$",
    ),
]


class SubjectType(str, Enum):
    MARKET = "market"
    SEGMENT = "segment"
    ENTITY = "entity"
    PRODUCT = "product"
    GEOGRAPHY = "geography"
    CUSTOMER = "customer"
    OTHER = "other"


class EvidenceKind(str, Enum):
    METRIC = "metric"
    FILING = "filing"
    SURVEY = "survey"
    REPORT = "report"
    ANNOUNCEMENT = "announcement"
    OBSERVATION = "observation"
    OTHER = "other"


class DirectionalSignal(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    STABLE = "stable"
    MIXED = "mixed"
    NONE = "none"


class MetricValueKind(str, Enum):
    ABSOLUTE = "absolute"
    COUNT = "count"
    RATIO = "ratio"
    PERCENTAGE = "percentage"
    PERCENTAGE_POINT_DELTA = "percentage_point_delta"
    CURRENCY = "currency"


class MetricScale(str, Enum):
    ONES = "ones"
    THOUSANDS = "thousands"
    MILLIONS = "millions"
    BILLIONS = "billions"


class MetricPeriodType(str, Enum):
    INSTANT = "instant"
    MONTH = "month"
    QUARTER = "quarter"
    HALF_YEAR = "half_year"
    FISCAL_YEAR = "fiscal_year"
    CALENDAR_YEAR = "calendar_year"
    TRAILING_TWELVE_MONTHS = "trailing_twelve_months"
    CUSTOM = "custom"


class AccountingBasis(str, Enum):
    GAAP = "gaap"
    IFRS = "ifrs"
    ADJUSTED = "adjusted"
    STATUTORY = "statutory"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class MetricDimension(StrictModel):
    metric_name: BoundedLabel
    metric_unit: BoundedLabel
    value_kind: MetricValueKind
    scale: MetricScale
    currency: CurrencyCode | None = None
    period_type: MetricPeriodType
    accounting_basis: AccountingBasis
    fiscal_calendar_id: OpaqueId | None = None

    @model_validator(mode="after")
    def validate_dimension(self) -> "MetricDimension":
        if self.value_kind == MetricValueKind.CURRENCY and self.currency is None:
            raise ValueError("currency is required when value_kind is currency")
        if self.value_kind != MetricValueKind.CURRENCY and self.currency is not None:
            raise ValueError("currency is forbidden unless value_kind is currency")
        if (
            self.value_kind == MetricValueKind.CURRENCY
            and self.metric_unit.upper() != self.currency
        ):
            raise ValueError("currency metric_unit must match the ISO currency")
        if self.value_kind == MetricValueKind.PERCENTAGE and self.metric_unit != "%":
            raise ValueError("percentage values must use metric_unit '%'")
        if (
            self.value_kind == MetricValueKind.PERCENTAGE_POINT_DELTA
            and self.metric_unit.casefold() != "percentage_points"
        ):
            raise ValueError(
                "percentage-point deltas must use metric_unit 'percentage_points'"
            )
        if (
            self.value_kind
            in {
                MetricValueKind.PERCENTAGE,
                MetricValueKind.RATIO,
                MetricValueKind.PERCENTAGE_POINT_DELTA,
            }
            and self.scale != MetricScale.ONES
        ):
            raise ValueError(
                "percentage, ratio, and percentage-point values require scale ones"
            )
        if (
            self.period_type == MetricPeriodType.FISCAL_YEAR
            and self.fiscal_calendar_id is None
        ):
            raise ValueError("fiscal_calendar_id is required for fiscal_year periods")
        return self


def _dimension_key(dimension: MetricDimension) -> tuple[str, ...]:
    return (
        dimension.metric_name.casefold(),
        dimension.metric_unit.casefold(),
        dimension.value_kind.value,
        dimension.scale.value,
        dimension.currency or "",
        dimension.period_type.value,
        dimension.accounting_basis.value,
        dimension.fiscal_calendar_id or "",
    )


def _validate_period_duration(
    period_type: MetricPeriodType,
    start: date | None,
    end: date,
) -> None:
    if period_type == MetricPeriodType.INSTANT:
        return
    assert start is not None
    elapsed_days = (end - start).days
    ranges = {
        MetricPeriodType.MONTH: (27, 31),
        MetricPeriodType.QUARTER: (80, 95),
        MetricPeriodType.HALF_YEAR: (170, 190),
        MetricPeriodType.FISCAL_YEAR: (350, 380),
        MetricPeriodType.CALENDAR_YEAR: (350, 380),
        MetricPeriodType.TRAILING_TWELVE_MONTHS: (350, 380),
        MetricPeriodType.CUSTOM: (1, 3_660),
    }
    minimum, maximum = ranges[period_type]
    if not minimum <= elapsed_days <= maximum:
        raise ValueError(
            f"{period_type.value} period duration must be between "
            f"{minimum} and {maximum} elapsed days"
        )
    if period_type == MetricPeriodType.CALENDAR_YEAR and not (
        start.month == 1
        and start.day == 1
        and end.month == 12
        and end.day == 31
        and start.year == end.year
    ):
        raise ValueError(
            "calendar_year periods must run from January 1 through December 31"
        )


class ResearchScope(StrictModel):
    scope_id: OpaqueId
    title: BoundedTitle
    start_date: date
    end_date: date
    subject_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=MAX_SCOPE_SUBJECTS)
    metric_dimensions: tuple[MetricDimension, ...] = Field(
        default_factory=tuple, max_length=MAX_SCOPE_METRICS
    )

    @model_validator(mode="after")
    def validate_scope(self) -> "ResearchScope":
        if self.end_date < self.start_date:
            raise ValueError("scope end_date must not precede start_date")
        if len(self.subject_ids) != len(set(self.subject_ids)):
            raise ValueError("scope subject_ids must be unique")
        dimensions = [_dimension_key(m) for m in self.metric_dimensions]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("scope metric dimensions must be unique ignoring case")
        return self


class MarketEvidence(StrictModel):
    evidence_id: OpaqueId
    source_id: OpaqueId
    subject_id: OpaqueId
    subject_type: SubjectType
    evidence_kind: EvidenceKind
    observed_date: date
    title: BoundedTitle
    statement: BoundedStatement
    metric_name: BoundedLabel | None = None
    metric_value: ExactDecimal | None = None
    metric_unit: BoundedLabel | None = None
    value_kind: MetricValueKind | None = None
    scale: MetricScale | None = None
    currency: CurrencyCode | None = None
    period_type: MetricPeriodType | None = None
    accounting_basis: AccountingBasis | None = None
    metric_period_start: date | None = None
    metric_period_end: date | None = None
    fiscal_calendar_id: OpaqueId | None = None
    directional_signal: DirectionalSignal = DirectionalSignal.NONE
    tags: tuple[Tag, ...] = Field(default_factory=tuple, max_length=MAX_TAGS)

    @model_validator(mode="after")
    def validate_metric_tuple(self) -> "MarketEvidence":
        core_fields = (
            self.metric_name,
            self.metric_value,
            self.metric_unit,
            self.value_kind,
            self.scale,
            self.period_type,
            self.accounting_basis,
        )
        optional_metric_fields = (
            self.currency,
            self.metric_period_start,
            self.metric_period_end,
            self.fiscal_calendar_id,
        )
        if any(value is not None for value in (*core_fields, *optional_metric_fields)) and not all(
            value is not None for value in core_fields
        ):
            raise ValueError(
                "metric value and all comparability metadata are all-or-none"
            )
        if self.metric_name is not None:
            MetricDimension(
                metric_name=self.metric_name,
                metric_unit=self.metric_unit,
                value_kind=self.value_kind,
                scale=self.scale,
                currency=self.currency,
                period_type=self.period_type,
                accounting_basis=self.accounting_basis,
                fiscal_calendar_id=self.fiscal_calendar_id,
            )
            if self.period_type == MetricPeriodType.INSTANT:
                if self.metric_period_start is not None:
                    raise ValueError(
                        "metric_period_start is forbidden for instant periods"
                    )
            elif self.metric_period_start is None:
                raise ValueError(
                    "metric_period_start is required for non-instant periods"
                )
            if self.metric_period_end is None:
                raise ValueError("metric_period_end is required for metric evidence")
            if self.metric_period_end > self.observed_date:
                raise ValueError(
                    "metric_period_end cannot follow observed_date in non-forecast v1"
                )
            if (
                self.metric_period_start is not None
                and self.metric_period_start >= self.metric_period_end
            ):
                raise ValueError(
                    "metric_period_start must precede metric_period_end"
                )
            _validate_period_duration(
                self.period_type,
                self.metric_period_start,
                self.metric_period_end,
            )
        folded = [tag.casefold() for tag in self.tags]
        if len(folded) != len(set(folded)):
            raise ValueError("tags must be unique ignoring case")
        return self


def _validate_metadata(metadata: AnalysisMetadata) -> dict[str, SourceMetadata]:
    sources = {source.source_id: source for source in metadata.sources}
    if len(sources) != len(metadata.sources):
        raise ValueError("metadata source_id values must be unique")
    if len(sources) > MAX_SOURCES:
        raise ValueError(f"at most {MAX_SOURCES} sources are supported")
    if any(source.as_of > metadata.as_of for source in metadata.sources):
        raise ValueError("source as_of cannot be later than analysis as_of")
    return sources


def _validate_research(
    metadata: AnalysisMetadata,
    scope: ResearchScope,
    evidence: tuple[MarketEvidence, ...],
) -> None:
    sources = _validate_metadata(metadata)
    if scope.end_date > metadata.as_of:
        raise ValueError("scope end_date cannot be later than analysis as_of")
    evidence_ids = [item.evidence_id for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence_id values must be unique")
    dimensions = {_metric_key(item)[1:] for item in evidence if item.metric_name is not None}
    if len(dimensions) > MAX_SCOPE_METRICS:
        raise ValueError(f"at most {MAX_SCOPE_METRICS} distinct metric dimensions are supported")
    declared_dimensions = {_dimension_key(item) for item in scope.metric_dimensions}
    undeclared = dimensions - declared_dimensions
    if undeclared:
        raise ValueError(
            "all market evidence metric dimensions must be declared in scope"
        )
    scope_subjects = set(scope.subject_ids)
    subject_types: dict[str, SubjectType] = {}
    for item in evidence:
        if item.source_id not in sources:
            raise ValueError("evidence source_id must reference metadata.sources")
        if item.subject_id not in scope_subjects:
            raise ValueError("evidence subject_id must be declared in scope.subject_ids")
        if not scope.start_date <= item.observed_date <= scope.end_date:
            raise ValueError("evidence observed_date must be within scope dates")
        if item.observed_date > metadata.as_of:
            raise ValueError("evidence observed_date cannot be later than analysis as_of")
        if item.observed_date > sources[item.source_id].as_of:
            raise ValueError("evidence observed_date cannot be later than its source as_of")
        previous = subject_types.setdefault(item.subject_id, item.subject_type)
        if previous != item.subject_type:
            raise ValueError("a subject_id must have one consistent subject_type")


class MarketResearchInput(StrictModel):
    metadata: AnalysisMetadata
    scope: ResearchScope
    evidence: tuple[MarketEvidence, ...] = Field(
        min_length=1, max_length=MAX_EVIDENCE_ITEMS
    )

    @model_validator(mode="after")
    def validate_input(self) -> "MarketResearchInput":
        _validate_research(self.metadata, self.scope, self.evidence)
        return self


class CompetitiveEntity(StrictModel):
    entity_id: OpaqueId
    name: BoundedTitle


class CompetitiveLandscapeInput(StrictModel):
    metadata: AnalysisMetadata
    scope: ResearchScope
    entities: tuple[CompetitiveEntity, ...] = Field(min_length=2, max_length=MAX_ENTITIES)
    evidence: tuple[MarketEvidence, ...] = Field(
        min_length=1, max_length=MAX_EVIDENCE_ITEMS
    )

    @model_validator(mode="after")
    def validate_input(self) -> "CompetitiveLandscapeInput":
        ids = [entity.entity_id for entity in self.entities]
        if len(ids) != len(set(ids)):
            raise ValueError("competitive entity IDs must be unique")
        if set(ids) != set(self.scope.subject_ids):
            raise ValueError("scope subject_ids must exactly match declared competitive entities")
        if any(item.subject_type != SubjectType.ENTITY for item in self.evidence):
            raise ValueError("competitive evidence subject_type must be entity")
        _validate_research(self.metadata, self.scope, self.evidence)
        return self


class EvidenceBinding(StrictModel):
    evidence_id: OpaqueId
    source_id: OpaqueId
    statement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_canonicalization_version: Literal[
        "grandice-market-evidence-canonical-v1"
    ] = EVIDENCE_CANONICALIZATION_VERSION
    excerpt: str = Field(min_length=1, max_length=MAX_EXCERPT_CHARS)
    provenance: Literal["caller_provided"] = "caller_provided"
    verification_status: Literal["unverified"] = "unverified"
    safe_rendering_required: Literal[True] = True
    render_as: Literal["text"] = "text"


class SourceCount(StrictModel):
    source_id: OpaqueId
    evidence_count: int = Field(strict=True, ge=0, le=MAX_EVIDENCE_ITEMS)
    share: ExactDecimal


class SourceConcentration(StrictModel):
    distinct_declared_source_id_count: int = Field(
        strict=True, ge=0, le=MAX_SOURCES
    )
    largest_declared_source_id_share: ExactDecimal
    declared_source_id_hhi: ExactDecimal


class SourceBinding(StrictModel):
    source_id: OpaqueId
    citation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: date
    provenance: Literal["caller_provided"] = "caller_provided"
    verification_status: Literal["unverified"] = "unverified"


class FreshnessSummary(StrictModel):
    days_0_30: int = Field(strict=True, ge=0)
    days_31_90: int = Field(strict=True, ge=0)
    days_91_365: int = Field(strict=True, ge=0)
    days_over_365: int = Field(strict=True, ge=0)


class SectionCounts(StrictModel):
    total: int = Field(strict=True, ge=0)
    emitted: int = Field(strict=True, ge=0)
    omitted: int = Field(strict=True, ge=0)


class ContradictionSummary(StrictModel):
    same_period_metric_disagreement_count: int = Field(strict=True, ge=0)
    material_metric_contradiction_count: int = Field(strict=True, ge=0)
    same_date_opposing_signal_count: int = Field(strict=True, ge=0)
    temporal_signal_reversal_count: int = Field(strict=True, ge=0)
    ambiguous_signal_transition_count: int = Field(strict=True, ge=0)


class ResearchGapSummary(StrictModel):
    no_evidence_count: int = Field(strict=True, ge=0)
    missing_metric_count: int = Field(strict=True, ge=0)
    single_source_count: int = Field(strict=True, ge=0)
    stale_evidence_count: int = Field(strict=True, ge=0)


class SignalSummary(StrictModel):
    positive: int = Field(strict=True, ge=0)
    negative: int = Field(strict=True, ge=0)
    stable: int = Field(strict=True, ge=0)
    mixed: int = Field(strict=True, ge=0)
    none: int = Field(strict=True, ge=0)


class MetricObservation(StrictModel):
    subject_id: OpaqueId
    metric_name: BoundedLabel
    metric_unit: BoundedLabel
    value_kind: MetricValueKind
    scale: MetricScale
    currency: CurrencyCode | None
    period_type: MetricPeriodType
    accounting_basis: AccountingBasis
    fiscal_calendar_id: OpaqueId | None
    metric_period_start: date | None
    metric_period_end: date
    observed_date: date
    value: ExactDecimal | None
    reported_values: tuple[ExactDecimal, ...] = Field(
        min_length=1, max_length=MAX_BINDINGS_PER_OUTPUT
    )
    total_reported_value_count: int = Field(strict=True, ge=1)
    omitted_reported_value_count: int = Field(strict=True, ge=0)
    disputed: bool
    period_start_disputed: bool
    bindings: tuple[EvidenceBinding, ...] = Field(
        min_length=1, max_length=MAX_BINDINGS_PER_OUTPUT
    )
    total_binding_count: int = Field(strict=True, ge=1)
    omitted_binding_count: int = Field(strict=True, ge=0)


class MetricTrend(StrictModel):
    subject_id: OpaqueId
    metric_name: BoundedLabel
    metric_unit: BoundedLabel
    value_kind: MetricValueKind
    scale: MetricScale
    currency: CurrencyCode | None
    period_type: MetricPeriodType
    accounting_basis: AccountingBasis
    fiscal_calendar_id: OpaqueId | None
    start_period_start: date | None
    end_period_start: date | None
    start_period_end: date
    end_period_end: date
    status: Literal["available", "disputed_endpoint"]
    start_value: ExactDecimal | None
    end_value: ExactDecimal | None
    interval_change: DerivedDecimal | None
    percentage_change: ExactDecimal | None
    cagr_inferred: Literal[False] = False
    bindings: tuple[EvidenceBinding, ...] = Field(
        min_length=2, max_length=MAX_BINDINGS_PER_OUTPUT
    )
    total_binding_count: int = Field(strict=True, ge=2)
    omitted_binding_count: int = Field(strict=True, ge=0)


class EvidenceContradiction(StrictModel):
    contradiction_type: Literal["metric_value", "directional_signal"]
    subject_id: OpaqueId
    metric_name: BoundedLabel | None = None
    metric_unit: BoundedLabel | None = None
    metric_dimension: MetricDimension | None = None
    observed_date: date | None = None
    metric_period_end: date | None = None
    observation_dates: tuple[date, ...] = Field(
        default_factory=tuple, max_length=MAX_BINDINGS_PER_OUTPUT
    )
    explanation: str = Field(min_length=1, max_length=500)
    bindings: tuple[EvidenceBinding, ...] = Field(
        min_length=2, max_length=MAX_BINDINGS_PER_OUTPUT
    )
    total_binding_count: int = Field(strict=True, ge=2)
    omitted_binding_count: int = Field(strict=True, ge=0)


class ResearchGap(StrictModel):
    subject_id: OpaqueId
    gap_type: Literal["no_evidence", "missing_metric", "single_source", "stale_evidence"]
    metric_name: BoundedLabel | None = None
    metric_unit: BoundedLabel | None = None
    metric_dimension: MetricDimension | None = None
    explanation: str = Field(min_length=1, max_length=500)
    bindings: tuple[EvidenceBinding, ...] = Field(
        default_factory=tuple, max_length=MAX_BINDINGS_PER_OUTPUT
    )
    total_binding_count: int = Field(strict=True, ge=0)
    omitted_binding_count: int = Field(strict=True, ge=0)


class CoverageScore(StrictModel):
    score: ExactDecimal
    subject_coverage: ExactDecimal
    expected_metric_coverage: ExactDecimal
    declared_source_id_diversity: ExactDecimal
    source_recency: ExactDecimal
    interpretation: Literal["evidence_coverage_not_truth_probability"] = (
        "evidence_coverage_not_truth_probability"
    )
    formula: str = (
        "25% subject coverage + 25% expected metric coverage + "
        "25% declared source-ID diversity + 25% referenced-source recency"
    )


class SubjectProfile(StrictModel):
    subject_id: OpaqueId
    evidence_count: int = Field(strict=True, ge=0)
    declared_source_id_count: int = Field(strict=True, ge=0)
    latest_observed_date: date | None
    observation_recency: FreshnessSummary
    source_recency: FreshnessSummary
    signals: SignalSummary
    latest_metrics: tuple[MetricObservation, ...] = Field(max_length=MAX_PROFILE_METRICS)
    latest_metric_counts: SectionCounts
    contradiction_count: int = Field(strict=True, ge=0)
    bindings: tuple[EvidenceBinding, ...] = Field(max_length=MAX_BINDINGS_PER_OUTPUT)
    total_binding_count: int = Field(strict=True, ge=0)
    omitted_binding_count: int = Field(strict=True, ge=0)


class MarketResearchAnalysis(AnalysisDisclosures):
    methodology_version: str = MARKET_METHODOLOGY_VERSION
    analysis_type: Literal["market_evidence_analysis"] = "market_evidence_analysis"
    source_citations: tuple[SourceMetadata, ...] = Field(min_length=1, max_length=MAX_SOURCES)
    source_bindings: tuple[SourceBinding, ...] = Field(
        min_length=1, max_length=MAX_SOURCES
    )
    analysis_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_manifest_version: Literal[
        "grandice-market-analysis-manifest-v1"
    ] = ANALYSIS_MANIFEST_VERSION
    scope: ResearchScope
    evidence_count: int = Field(strict=True, ge=1, le=MAX_EVIDENCE_ITEMS)
    declared_source_id_counts: tuple[SourceCount, ...] = Field(
        max_length=MAX_SOURCES
    )
    source_concentration: SourceConcentration
    observation_recency: FreshnessSummary
    source_recency: FreshnessSummary
    signals: SignalSummary
    coverage: CoverageScore
    latest_metrics: tuple[MetricObservation, ...] = Field(max_length=MAX_LATEST_METRICS)
    trends: tuple[MetricTrend, ...] = Field(max_length=MAX_TRENDS)
    contradictions: tuple[EvidenceContradiction, ...] = Field(max_length=MAX_CONTRADICTIONS)
    research_gaps: tuple[ResearchGap, ...] = Field(max_length=MAX_GAPS)
    latest_metric_counts: SectionCounts
    trend_counts: SectionCounts
    contradiction_counts: SectionCounts
    research_gap_counts: SectionCounts
    contradiction_summary: ContradictionSummary
    research_gap_summary: ResearchGapSummary
    complete: bool
    omitted_output_count: int = Field(strict=True, ge=0)
    omitted_binding_count: int = Field(strict=True, ge=0)
    evidence_provenance: Literal["caller_provided"] = "caller_provided"
    verification_status: Literal["unverified"] = "unverified"
    safe_rendering_required: Literal[True] = True
    render_as: Literal["text"] = "text"
    market_research_advice: Literal[False] = False
    content_persisted: Literal[False] = False


class EntityMetricValue(StrictModel):
    entity_id: OpaqueId
    observed_date: date
    metric_period_start: date | None
    metric_period_end: date
    value: ExactDecimal | None
    reported_values: tuple[ExactDecimal, ...] = Field(
        min_length=1, max_length=MAX_BINDINGS_PER_OUTPUT
    )
    total_reported_value_count: int = Field(strict=True, ge=1)
    omitted_reported_value_count: int = Field(strict=True, ge=0)
    disputed: bool
    period_start_disputed: bool


class CompetitiveMetricComparison(StrictModel):
    dimension: MetricDimension
    values: tuple[EntityMetricValue, ...] = Field(max_length=MAX_ENTITIES)
    missing_entity_ids: tuple[OpaqueId, ...] = Field(max_length=MAX_ENTITIES)
    comparable: bool
    incomparability_reasons: tuple[BoundedLabel, ...] = Field(
        max_length=MAX_INCOMPARABILITY_REASONS
    )
    ranking_performed: Literal[False] = False
    bindings: tuple[EvidenceBinding, ...] = Field(
        min_length=1, max_length=MAX_BINDINGS_PER_OUTPUT
    )
    total_binding_count: int = Field(strict=True, ge=1)
    omitted_binding_count: int = Field(strict=True, ge=0)


class IncomparableMetricDimension(StrictModel):
    dimension: MetricDimension
    reasons: tuple[BoundedLabel, ...] = Field(
        min_length=1, max_length=MAX_INCOMPARABILITY_REASONS
    )
    present_entity_ids: tuple[OpaqueId, ...] = Field(max_length=MAX_ENTITIES)
    missing_entity_ids: tuple[OpaqueId, ...] = Field(max_length=MAX_ENTITIES)


class CompetitiveLandscapeAnalysis(AnalysisDisclosures):
    methodology_version: str = MARKET_METHODOLOGY_VERSION
    analysis_type: Literal["competitive_landscape_comparison"] = (
        "competitive_landscape_comparison"
    )
    source_citations: tuple[SourceMetadata, ...] = Field(min_length=1, max_length=MAX_SOURCES)
    source_bindings: tuple[SourceBinding, ...] = Field(
        min_length=1, max_length=MAX_SOURCES
    )
    analysis_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_manifest_version: Literal[
        "grandice-market-analysis-manifest-v1"
    ] = ANALYSIS_MANIFEST_VERSION
    scope: ResearchScope
    entity_profiles: tuple[SubjectProfile, ...] = Field(min_length=2, max_length=MAX_ENTITIES)
    metric_comparisons: tuple[CompetitiveMetricComparison, ...] = Field(max_length=MAX_OUTPUT_ROWS)
    incomparable_dimensions: tuple[IncomparableMetricDimension, ...] = Field(
        max_length=MAX_OUTPUT_ROWS
    )
    missing_evidence_entity_ids: tuple[OpaqueId, ...] = Field(max_length=MAX_ENTITIES)
    profile_counts: SectionCounts
    metric_comparison_counts: SectionCounts
    incomparable_dimension_counts: SectionCounts
    contradiction_summary: ContradictionSummary
    observation_recency: FreshnessSummary
    source_recency: FreshnessSummary
    complete: bool
    omitted_output_count: int = Field(strict=True, ge=0)
    omitted_binding_count: int = Field(strict=True, ge=0)
    evidence_provenance: Literal["caller_provided"] = "caller_provided"
    verification_status: Literal["unverified"] = "unverified"
    safe_rendering_required: Literal[True] = True
    render_as: Literal["text"] = "text"
    market_research_advice: Literal[False] = False
    strategic_recommendations_generated: Literal[False] = False
    content_persisted: Literal[False] = False


def _binding(item: MarketEvidence) -> EvidenceBinding:
    excerpt = " ".join(item.statement.split())[:MAX_EXCERPT_CHARS]
    canonical_record = {
        "accounting_basis": (
            item.accounting_basis.value if item.accounting_basis else None
        ),
        "currency": item.currency,
        "directional_signal": item.directional_signal.value,
        "evidence_id": item.evidence_id,
        "evidence_kind": item.evidence_kind.value,
        "fiscal_calendar_id": item.fiscal_calendar_id,
        "metric_name": item.metric_name,
        "metric_period_end": (
            item.metric_period_end.isoformat() if item.metric_period_end else None
        ),
        "metric_period_start": (
            item.metric_period_start.isoformat() if item.metric_period_start else None
        ),
        "metric_unit": item.metric_unit,
        "metric_value": str(item.metric_value)
        if item.metric_value is not None
        else None,
        "observed_date": item.observed_date.isoformat(),
        "period_type": item.period_type.value if item.period_type else None,
        "scale": item.scale.value if item.scale else None,
        "source_id": item.source_id,
        "statement": item.statement,
        "subject_id": item.subject_id,
        "subject_type": item.subject_type.value,
        "tags": list(item.tags),
        "title": item.title,
        "value_kind": item.value_kind.value if item.value_kind else None,
        "version": EVIDENCE_CANONICALIZATION_VERSION,
    }
    canonical_bytes = json.dumps(
        canonical_record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return EvidenceBinding(
        evidence_id=item.evidence_id,
        source_id=item.source_id,
        statement_sha256=hashlib.sha256(item.statement.encode("utf-8")).hexdigest(),
        evidence_sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        excerpt=excerpt,
    )


def _analysis_provenance(
    metadata: AnalysisMetadata,
    scope: ResearchScope,
    evidence: tuple[MarketEvidence, ...],
    *,
    analysis_type: str,
    validated_input: StrictModel,
) -> tuple[tuple[SourceBinding, ...], str]:
    source_bindings = tuple(
        SourceBinding(
            source_id=source.source_id,
            citation_sha256=hashlib.sha256(
                source.citation.encode("utf-8")
            ).hexdigest(),
            as_of=source.as_of,
        )
        for source in sorted(metadata.sources, key=lambda item: item.source_id)
    )
    evidence_digests = [
        {
            "evidence_id": item.evidence_id,
            "evidence_sha256": _binding(item).evidence_sha256,
        }
        for item in sorted(evidence, key=lambda item: item.evidence_id)
    ]
    canonical_input = validated_input.model_dump(mode="json")
    canonical_input["metadata"]["sources"] = sorted(
        canonical_input["metadata"]["sources"],
        key=lambda item: item["source_id"],
    )
    canonical_input["evidence"] = sorted(
        canonical_input["evidence"],
        key=lambda item: item["evidence_id"],
    )
    manifest = {
        "manifest_version": ANALYSIS_MANIFEST_VERSION,
        "methodology_version": MARKET_METHODOLOGY_VERSION,
        "analysis_type": analysis_type,
        "analysis_as_of": metadata.as_of.isoformat(),
        "validated_input": canonical_input,
        "scope": scope.model_dump(mode="json"),
        "source_bindings": [
            binding.model_dump(mode="json") for binding in source_bindings
        ],
        "evidence_digests": evidence_digests,
    }
    encoded = json.dumps(
        manifest,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return source_bindings, hashlib.sha256(encoded).hexdigest()


def _bounded_bindings(
    items: list[MarketEvidence] | tuple[MarketEvidence, ...],
    limit: int = MAX_BINDINGS_PER_OUTPUT,
) -> tuple[tuple[EvidenceBinding, ...], int, int]:
    ordered = sorted(items, key=lambda item: item.evidence_id)
    selected = ordered[:limit]
    return (
        tuple(_binding(item) for item in selected),
        len(ordered),
        max(0, len(ordered) - len(selected)),
    )


def _recency(dates: tuple[date, ...] | list[date], as_of: date) -> FreshnessSummary:
    buckets = [0, 0, 0, 0]
    for value in dates:
        age = (as_of - value).days
        index = 0 if age <= 30 else 1 if age <= 90 else 2 if age <= 365 else 3
        buckets[index] += 1
    return FreshnessSummary(
        days_0_30=buckets[0],
        days_31_90=buckets[1],
        days_91_365=buckets[2],
        days_over_365=buckets[3],
    )


def _observation_recency(
    items: tuple[MarketEvidence, ...], as_of: date
) -> FreshnessSummary:
    return _recency([item.observed_date for item in items], as_of)


def _source_recency(
    items: tuple[MarketEvidence, ...],
    metadata: AnalysisMetadata,
) -> FreshnessSummary:
    referenced = {item.source_id for item in items}
    dates = [
        source.as_of for source in metadata.sources if source.source_id in referenced
    ]
    return _recency(dates, metadata.as_of)


def _signals(items: tuple[MarketEvidence, ...]) -> SignalSummary:
    counts = Counter(item.directional_signal.value for item in items)
    return SignalSummary(**{name: counts[name] for name in ("positive", "negative", "stable", "mixed", "none")})


def _metric_key(item: MarketEvidence) -> tuple[str, ...]:
    assert (
        item.metric_name is not None
        and item.metric_unit is not None
        and item.value_kind is not None
        and item.scale is not None
        and item.period_type is not None
        and item.accounting_basis is not None
    )
    return (
        item.subject_id,
        item.metric_name.casefold(),
        item.metric_unit.casefold(),
        item.value_kind.value,
        item.scale.value,
        item.currency or "",
        item.period_type.value,
        item.accounting_basis.value,
        item.fiscal_calendar_id or "",
    )


def _item_dimension(item: MarketEvidence) -> MetricDimension:
    assert (
        item.metric_name is not None
        and item.metric_unit is not None
        and item.value_kind is not None
        and item.scale is not None
        and item.period_type is not None
        and item.accounting_basis is not None
    )
    return MetricDimension(
        metric_name=item.metric_name,
        metric_unit=item.metric_unit,
        value_kind=item.value_kind,
        scale=item.scale,
        currency=item.currency,
        period_type=item.period_type,
        accounting_basis=item.accounting_basis,
        fiscal_calendar_id=item.fiscal_calendar_id,
    )


def _metric_groups(
    items: tuple[MarketEvidence, ...],
) -> dict[tuple[str, ...], list[MarketEvidence]]:
    groups: dict[tuple[str, ...], list[MarketEvidence]] = defaultdict(list)
    for item in items:
        if item.metric_name is not None:
            groups[_metric_key(item)].append(item)
    for group in groups.values():
        group.sort(
            key=lambda item: (
                item.metric_period_end,
                item.observed_date,
                item.evidence_id,
            )
        )
    return groups


def _latest_metrics(
    groups: dict[tuple[str, ...], list[MarketEvidence]],
    limit: int,
    binding_limit: int = MAX_BINDINGS_PER_OUTPUT,
) -> tuple[list[MetricObservation], int]:
    rows: list[MetricObservation] = []
    total = len(groups)
    for key in sorted(groups):
        if len(rows) >= limit:
            break
        group = groups[key]
        latest_period_end = group[-1].metric_period_end
        latest = [
            item for item in group
            if item.metric_period_end == latest_period_end
        ]
        representative = latest[-1]
        values = sorted({item.metric_value for item in latest if item.metric_value is not None})
        period_start_disputed = (
            len({item.metric_period_start for item in latest}) > 1
        )
        disputed = len(values) > 1 or period_start_disputed
        bindings, total_bindings, omitted_bindings = _bounded_bindings(
            latest, binding_limit
        )
        visible_values = tuple(values[:MAX_BINDINGS_PER_OUTPUT])
        dimension = _item_dimension(representative)
        rows.append(
            MetricObservation(
                subject_id=key[0],
                **dimension.model_dump(),
                observed_date=max(item.observed_date for item in latest),
                metric_period_start=representative.metric_period_start,
                metric_period_end=latest_period_end,
                value=None if disputed else values[0],
                reported_values=visible_values,
                total_reported_value_count=len(values),
                omitted_reported_value_count=len(values) - len(visible_values),
                disputed=disputed,
                period_start_disputed=period_start_disputed,
                bindings=bindings,
                total_binding_count=total_bindings,
                omitted_binding_count=omitted_bindings,
            )
        )
    return rows, total


def _trends(
    groups: dict[tuple[str, ...], list[MarketEvidence]],
    limit: int,
) -> tuple[list[MetricTrend], int]:
    rows: list[MetricTrend] = []
    total = 0
    for key in sorted(groups):
        dated: dict[date, list[MarketEvidence]] = defaultdict(list)
        for item in groups[key]:
            dated[item.metric_period_end].append(item)
        ordered = sorted(dated.items())
        for (start_date, start_items), (end_date, end_items) in zip(
            ordered, ordered[1:]
        ):
            total += 1
            if len(rows) >= limit:
                continue
            start_values = sorted(
                {item.metric_value for item in start_items if item.metric_value is not None}
            )
            end_values = sorted(
                {item.metric_value for item in end_items if item.metric_value is not None}
            )
            disputed = (
                len(start_values) > 1
                or len(end_values) > 1
                or len({item.metric_period_start for item in start_items}) > 1
                or len({item.metric_period_start for item in end_items}) > 1
            )
            start_value = None if disputed else start_values[0]
            end_value = None if disputed else end_values[0]
            with localcontext() as context:
                context.prec = DECIMAL_WORK_PRECISION
                change = (
                    None if disputed else end_value - start_value
                )
                percentage = (
                    None
                    if disputed or start_value == 0
                    else quantize(
                        divide(change, start_value) * Decimal("100")
                    )
                )
            representative = start_items[0]
            dimension = _item_dimension(representative)
            bindings, total_bindings, omitted_bindings = _bounded_bindings(
                start_items + end_items
            )
            rows.append(
                MetricTrend(
                    subject_id=key[0],
                    **dimension.model_dump(),
                    start_period_start=start_items[0].metric_period_start,
                    end_period_start=end_items[0].metric_period_start,
                    start_period_end=start_date,
                    end_period_end=end_date,
                    status="disputed_endpoint" if disputed else "available",
                    start_value=start_value,
                    end_value=end_value,
                    interval_change=change,
                    percentage_change=percentage,
                    bindings=bindings,
                    total_binding_count=total_bindings,
                    omitted_binding_count=omitted_bindings,
                )
            )
    return rows, total


def _contradictions(
    items: tuple[MarketEvidence, ...],
    limit: int,
) -> tuple[
    list[EvidenceContradiction],
    int,
    ContradictionSummary,
    Counter[str],
]:
    rows: list[EvidenceContradiction] = []
    metric_dates: dict[tuple[str, ...], list[MarketEvidence]] = defaultdict(list)
    direction_dates: dict[tuple[str, ...], list[MarketEvidence]] = defaultdict(list)
    direction_history: dict[
        tuple[str, ...], dict[date, set[DirectionalSignal]]
    ] = defaultdict(lambda: defaultdict(set))
    for item in items:
        if item.metric_name is not None:
            metric_dates[
                (
                    *_metric_key(item),
                    item.metric_period_start.isoformat()
                    if item.metric_period_start
                    else "",
                    item.metric_period_end.isoformat(),
                )
            ].append(item)
        direction_dimension = _metric_key(item)[1:] if item.metric_name else ("",)
        direction_key = (item.subject_id, *direction_dimension)
        if item.directional_signal != DirectionalSignal.NONE:
            direction_history[direction_key][item.observed_date].add(
                item.directional_signal
            )
        if item.directional_signal in {DirectionalSignal.POSITIVE, DirectionalSignal.NEGATIVE}:
            direction_dates[(*direction_key, item.observed_date.isoformat())].append(
                item
            )
    disagreement_count = 0
    material_count = 0
    same_date_signal_count = 0
    temporal_reversal_count = 0
    ambiguous_transition_count = 0
    subject_counts: Counter[str] = Counter()
    for key in sorted(metric_dates):
        group = metric_dates[key]
        values = sorted(
            {item.metric_value for item in group if item.metric_value is not None}
        )
        if len(values) < 2:
            continue
        disagreement_count += 1
        with localcontext() as context:
            context.prec = DECIMAL_WORK_PRECISION
            spread = max(values) - min(values)
            magnitude = max(abs(value) for value in values)
            threshold = max(
                Decimal("0.000001"), magnitude * Decimal("0.01")
            )
        if spread >= threshold:
            material_count += 1
            subject_counts[key[0]] += 1
            if len(rows) >= limit:
                continue
            first = group[0]
            bindings, total_bindings, omitted_bindings = _bounded_bindings(group)
            rows.append(
                EvidenceContradiction(
                    contradiction_type="metric_value",
                    subject_id=key[0],
                    metric_name=first.metric_name,
                    metric_unit=first.metric_unit,
                    metric_dimension=_item_dimension(first),
                    metric_period_end=first.metric_period_end,
                    observation_dates=tuple(
                        sorted({item.observed_date for item in group})[
                            :MAX_BINDINGS_PER_OUTPUT
                        ]
                    ),
                    explanation="Values for the same metric period differ by at least the fixed 1% materiality threshold (minimum 0.000001).",
                    bindings=bindings,
                    total_binding_count=total_bindings,
                    omitted_binding_count=omitted_bindings,
                )
            )
    for key in sorted(direction_dates):
        group = direction_dates[key]
        present = {item.directional_signal for item in group}
        if {DirectionalSignal.POSITIVE, DirectionalSignal.NEGATIVE} <= present:
            same_date_signal_count += 1
            subject_counts[key[0]] += 1
            if len(rows) >= limit:
                continue
            first = group[0]
            bindings, total_bindings, omitted_bindings = _bounded_bindings(group)
            rows.append(
                EvidenceContradiction(
                    contradiction_type="directional_signal",
                    subject_id=key[0],
                    metric_name=first.metric_name,
                    metric_unit=first.metric_unit,
                    metric_dimension=(
                        _item_dimension(first) if first.metric_name is not None else None
                    ),
                    observed_date=first.observed_date,
                    explanation="Caller evidence contains opposing positive and negative signals on the same observation date.",
                    bindings=bindings,
                    total_binding_count=total_bindings,
                    omitted_binding_count=omitted_bindings,
                )
            )
    for dated_signals in direction_history.values():
        ordered = sorted(dated_signals.items())
        for (_old_date, old), (_new_date, new) in zip(ordered, ordered[1:]):
            old_signal = next(iter(old)) if len(old) == 1 else None
            new_signal = next(iter(new)) if len(new) == 1 else None
            if old_signal == DirectionalSignal.MIXED:
                old_signal = None
            if new_signal == DirectionalSignal.MIXED:
                new_signal = None
            if old_signal is None or new_signal is None:
                ambiguous_transition_count += 1
            elif {old_signal, new_signal} == {
                DirectionalSignal.POSITIVE,
                DirectionalSignal.NEGATIVE,
            }:
                temporal_reversal_count += 1
    total = material_count + same_date_signal_count
    return (
        rows,
        total,
        ContradictionSummary(
            same_period_metric_disagreement_count=disagreement_count,
            material_metric_contradiction_count=material_count,
            same_date_opposing_signal_count=same_date_signal_count,
            temporal_signal_reversal_count=temporal_reversal_count,
            ambiguous_signal_transition_count=ambiguous_transition_count,
        ),
        subject_counts,
    )


def _gaps(
    data: MarketResearchInput,
    limit: int,
) -> tuple[list[ResearchGap], int, ResearchGapSummary]:
    by_subject: dict[str, list[MarketEvidence]] = defaultdict(list)
    for item in data.evidence:
        by_subject[item.subject_id].append(item)
    gaps: list[ResearchGap] = []
    total = 0
    counts: Counter[str] = Counter()
    for subject_id in data.scope.subject_ids:
        items = by_subject[subject_id]
        bindings, total_bindings, omitted_bindings = _bounded_bindings(items)

        def add_gap(**values: object) -> None:
            nonlocal total
            total += 1
            counts[str(values["gap_type"])] += 1
            if len(gaps) < limit:
                gaps.append(ResearchGap(**values))

        if not items:
            add_gap(
                subject_id=subject_id,
                gap_type="no_evidence",
                explanation="No caller-provided evidence covers this declared subject.",
                total_binding_count=0,
                omitted_binding_count=0,
            )
            continue
        if len({item.source_id for item in items}) == 1:
            add_gap(
                subject_id=subject_id,
                gap_type="single_source",
                explanation="All evidence for this subject comes from one caller-declared source.",
                bindings=bindings,
                total_binding_count=total_bindings,
                omitted_binding_count=omitted_bindings,
            )
        if all((data.metadata.as_of - item.observed_date).days > 365 for item in items):
            add_gap(
                subject_id=subject_id,
                gap_type="stale_evidence",
                explanation="All evidence for this subject is more than 365 days old.",
                bindings=bindings,
                total_binding_count=total_bindings,
                omitted_binding_count=omitted_bindings,
            )
        present = {
            _metric_key(item)[1:] for item in items if item.metric_name is not None
        }
        for dimension in data.scope.metric_dimensions:
            if _dimension_key(dimension) not in present:
                add_gap(
                    subject_id=subject_id,
                    gap_type="missing_metric",
                    metric_name=dimension.metric_name,
                    metric_unit=dimension.metric_unit,
                    metric_dimension=dimension,
                    explanation="No evidence matches every caller-declared comparability dimension.",
                    bindings=bindings,
                    total_binding_count=total_bindings,
                    omitted_binding_count=omitted_bindings,
                )
    return (
        gaps,
        total,
        ResearchGapSummary(
            no_evidence_count=counts["no_evidence"],
            missing_metric_count=counts["missing_metric"],
            single_source_count=counts["single_source"],
            stale_evidence_count=counts["stale_evidence"],
        ),
    )


def _coverage(data: MarketResearchInput) -> CoverageScore:
    covered_subjects = len({item.subject_id for item in data.evidence})
    subject_ratio = divide(Decimal(covered_subjects), Decimal(len(data.scope.subject_ids)))
    expected = len(data.scope.subject_ids) * len(data.scope.metric_dimensions)
    if expected:
        expected_keys = {
            (subject_id, *_dimension_key(dimension))
            for subject_id in data.scope.subject_ids
            for dimension in data.scope.metric_dimensions
        }
        observed = {
            _metric_key(item)
            for item in data.evidence
            if item.metric_name is not None
        } & expected_keys
        metric_ratio = divide(Decimal(len(observed)), Decimal(expected))
        metric_ratio = min(Decimal("1"), metric_ratio)
    else:
        metric_ratio = Decimal("1")
    distinct_sources = len({item.source_id for item in data.evidence})
    source_ratio = min(Decimal("1"), divide(Decimal(distinct_sources), Decimal("3")))
    referenced_sources = {item.source_id for item in data.evidence}
    source_dates = [
        source.as_of
        for source in data.metadata.sources
        if source.source_id in referenced_sources
    ]
    recent_sources = sum(
        1
        for source_date in source_dates
        if (data.metadata.as_of - source_date).days <= 90
    )
    source_recency_ratio = divide(
        Decimal(recent_sources), Decimal(len(source_dates))
    )
    components = [
        quantize(value * Decimal("25"))
        for value in (
            subject_ratio,
            metric_ratio,
            source_ratio,
            source_recency_ratio,
        )
    ]
    return CoverageScore(
        score=sum(components, Decimal("0")),
        subject_coverage=components[0],
        expected_metric_coverage=components[1],
        declared_source_id_diversity=components[2],
        source_recency=components[3],
    )


def _source_stats(items: tuple[MarketEvidence, ...]) -> tuple[tuple[SourceCount, ...], SourceConcentration]:
    counts = Counter(item.source_id for item in items)
    total = Decimal(len(items))
    rows = tuple(
        SourceCount(source_id=source_id, evidence_count=count, share=quantize(divide(Decimal(count), total)))
        for source_id, count in sorted(counts.items())
    )
    shares = [row.share for row in rows]
    return rows, SourceConcentration(
        distinct_declared_source_id_count=len(rows),
        largest_declared_source_id_share=max(shares, default=Decimal("0")),
        declared_source_id_hhi=quantize(
            sum((share * share for share in shares), Decimal("0"))
        ),
    )


def _bounded_sections(*sections: list[object]) -> tuple[list[list[object]], int]:
    remaining = MAX_OUTPUT_ROWS
    bounded: list[list[object]] = []
    omitted = 0
    for section in sections:
        selected = section[:remaining]
        bounded.append(selected)
        omitted += len(section) - len(selected)
        remaining -= len(selected)
    return bounded, omitted


def _reported_binding_omissions(rows: list[object] | tuple[object, ...]) -> int:
    return sum(
        int(getattr(row, "omitted_binding_count", 0))
        + int(getattr(row, "omitted_reported_value_count", 0))
        for row in rows
    )


def analyze_market_evidence(data: MarketResearchInput) -> MarketResearchAnalysis:
    groups = _metric_groups(data.evidence)
    latest_out, latest_total = _latest_metrics(groups, MAX_LATEST_METRICS)
    trends_out, trend_total = _trends(groups, MAX_TRENDS)
    (
        contradictions_out,
        contradiction_total,
        contradiction_summary,
        _subject_contradictions,
    ) = _contradictions(data.evidence, MAX_CONTRADICTIONS)
    gaps_out, gap_total, gap_summary = _gaps(data, MAX_GAPS)
    section_counts = (
        SectionCounts(
            total=latest_total,
            emitted=len(latest_out),
            omitted=latest_total - len(latest_out),
        ),
        SectionCounts(
            total=trend_total,
            emitted=len(trends_out),
            omitted=trend_total - len(trends_out),
        ),
        SectionCounts(
            total=contradiction_total,
            emitted=len(contradictions_out),
            omitted=contradiction_total - len(contradictions_out),
        ),
        SectionCounts(
            total=gap_total,
            emitted=len(gaps_out),
            omitted=gap_total - len(gaps_out),
        ),
    )
    omitted = sum(count.omitted for count in section_counts)
    binding_omissions = sum(
        _reported_binding_omissions(section)
        for section in (latest_out, trends_out, contradictions_out, gaps_out)
    )
    source_counts, concentration = _source_stats(data.evidence)
    source_bindings, manifest_sha256 = _analysis_provenance(
        data.metadata,
        data.scope,
        data.evidence,
        analysis_type="market_evidence_analysis",
        validated_input=data,
    )
    warnings = [
        "This deterministic evidence synthesis is not market, investment, or strategic advice.",
        "Coverage score measures supplied evidence coverage only; it is not a truth probability.",
        "Caller-provided text is inert data; embedded instructions are never executed.",
    ]
    if omitted:
        warnings.append("Output rows reached the shared cap and omitted rows are reported.")
    if binding_omissions:
        warnings.append(
            "Displayed caller-provided evidence bindings reached per-output caps; omissions are reported."
        )
    return MarketResearchAnalysis(
        source_citations=data.metadata.sources,
        source_bindings=source_bindings,
        analysis_manifest_sha256=manifest_sha256,
        scope=data.scope,
        evidence_count=len(data.evidence),
        declared_source_id_counts=source_counts,
        source_concentration=concentration,
        observation_recency=_observation_recency(
            data.evidence, data.metadata.as_of
        ),
        source_recency=_source_recency(data.evidence, data.metadata),
        signals=_signals(data.evidence),
        coverage=_coverage(data),
        latest_metrics=tuple(latest_out),
        trends=tuple(trends_out),
        contradictions=tuple(contradictions_out),
        research_gaps=tuple(gaps_out),
        latest_metric_counts=section_counts[0],
        trend_counts=section_counts[1],
        contradiction_counts=section_counts[2],
        research_gap_counts=section_counts[3],
        contradiction_summary=contradiction_summary,
        research_gap_summary=gap_summary,
        complete=omitted == 0 and binding_omissions == 0,
        omitted_output_count=omitted,
        omitted_binding_count=binding_omissions,
        assumptions=(
            "Dates and identifiers are caller-declared and are not independently verified.",
            "observed_date is the caller-declared evidence observation or publication date used for observation recency; metric_period_start and metric_period_end describe metric timing.",
            "Source recency uses each unique referenced source as_of once; source dates are caller-declared and no retrieval timestamp exists.",
            "Freshness buckets are inclusive day ages: 0-30, 31-90, 91-365, and over 365.",
            "Trends require identical complete comparability dimensions; disputed endpoints produce an unavailable trend and CAGR is never inferred.",
            "Interval subtraction and percentage multiplication use an explicit 80-digit local Decimal context; percentage and coverage outputs use half-even quantization to six decimal places.",
            "A percentage value is expressed in percentage units: '12.5' means 12.5%, not a 0.125 ratio.",
            "Metric contradictions use a fixed 1% spread threshold with an absolute minimum of 0.000001.",
        ),
        limitations=(
            "Results describe only supplied structured evidence and cannot establish market truth, causation, completeness, or forecast accuracy.",
            "Metric dimensions are compared exactly (labels case-insensitively); values, scales, currencies, periods, and accounting bases are never converted or normalized.",
            "Latest same-period differing values are reported as disputed with no authoritative value selected.",
            "No retrieval timestamp is available, so source recency does not establish when evidence was obtained or independently verified.",
            "Caller-declared source IDs, citations, and dates can be duplicated across records or spoofed and do not establish source independence.",
            "Analysis is transient and in-memory; this module performs no persistence, network, browser, model, or tool calls.",
        ),
        warnings=tuple(warnings),
    )


def _profiles(
    data: CompetitiveLandscapeInput,
    contradiction_counts: Counter[str],
) -> tuple[SubjectProfile, ...]:
    by_subject: dict[str, list[MarketEvidence]] = defaultdict(list)
    for item in data.evidence:
        by_subject[item.subject_id].append(item)
    profiles: list[SubjectProfile] = []
    for entity in data.entities:
        items = tuple(sorted(by_subject[entity.entity_id], key=lambda item: (item.observed_date, item.evidence_id)))
        latest, latest_total = _latest_metrics(
            _metric_groups(items),
            MAX_PROFILE_METRICS,
            binding_limit=1,
        )
        bindings, total_bindings, omitted_bindings = _bounded_bindings(
            items, MAX_PROFILE_BINDINGS
        )
        profiles.append(
            SubjectProfile(
                subject_id=entity.entity_id,
                evidence_count=len(items),
                declared_source_id_count=len(
                    {item.source_id for item in items}
                ),
                latest_observed_date=max((item.observed_date for item in items), default=None),
                observation_recency=_observation_recency(
                    items, data.metadata.as_of
                ),
                source_recency=_source_recency(items, data.metadata),
                signals=_signals(items),
                latest_metrics=tuple(latest),
                latest_metric_counts=SectionCounts(
                    total=latest_total,
                    emitted=len(latest),
                    omitted=latest_total - len(latest),
                ),
                contradiction_count=contradiction_counts[entity.entity_id],
                bindings=bindings,
                total_binding_count=total_bindings,
                omitted_binding_count=omitted_bindings,
            )
        )
    return tuple(profiles)


def compare_competitive_landscape(
    data: CompetitiveLandscapeInput,
) -> CompetitiveLandscapeAnalysis:
    groups = _metric_groups(data.evidence)
    latest, _latest_total = _latest_metrics(
        groups, MAX_EVIDENCE_ITEMS, binding_limit=1
    )
    by_dimension: dict[tuple[str, ...], list[MetricObservation]] = defaultdict(list)
    for item in latest:
        dimension = MetricDimension(
            metric_name=item.metric_name,
            metric_unit=item.metric_unit,
            value_kind=item.value_kind,
            scale=item.scale,
            currency=item.currency,
            period_type=item.period_type,
            accounting_basis=item.accounting_basis,
            fiscal_calendar_id=item.fiscal_calendar_id,
        )
        by_dimension[_dimension_key(dimension)].append(item)
    entity_ids = tuple(entity.entity_id for entity in data.entities)
    comparisons: list[CompetitiveMetricComparison] = []
    incomparable: list[IncomparableMetricDimension] = []
    variants_by_name: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for dimension_key in by_dimension:
        variants_by_name[dimension_key[0]].append(dimension_key)
    mismatch_fields = (
        (1, "metric_unit_mismatch"),
        (2, "value_kind_mismatch"),
        (3, "scale_mismatch"),
        (4, "currency_mismatch"),
        (5, "period_type_mismatch"),
        (6, "accounting_basis_mismatch"),
        (7, "fiscal_calendar_mismatch"),
    )
    for dimension in sorted(by_dimension):
        values = sorted(by_dimension[dimension], key=lambda row: entity_ids.index(row.subject_id))
        present = {row.subject_id for row in values}
        missing = tuple(entity_id for entity_id in entity_ids if entity_id not in present)
        representative = values[0]
        metric_dimension = MetricDimension(
            metric_name=representative.metric_name,
            metric_unit=representative.metric_unit,
            value_kind=representative.value_kind,
            scale=representative.scale,
            currency=representative.currency,
            period_type=representative.period_type,
            accounting_basis=representative.accounting_basis,
            fiscal_calendar_id=representative.fiscal_calendar_id,
        )
        reasons: list[str] = []
        if len(values) < 2:
            reasons.append("fewer_than_two_entities")
        if missing:
            reasons.append("missing_entities")
        if len({row.metric_period_end for row in values}) > 1:
            reasons.append("metric_period_end_mismatch")
        if len({row.metric_period_start for row in values}) > 1:
            reasons.append("metric_period_start_mismatch")
        if any(row.disputed for row in values):
            reasons.append("disputed_latest_value")
        if representative.accounting_basis == AccountingBasis.UNKNOWN:
            reasons.append("unknown_accounting_basis")
        variants = variants_by_name[dimension[0]]
        for index, reason in mismatch_fields:
            if len({variant[index] for variant in variants}) > 1:
                reasons.append(reason)
        comparable = not reasons
        all_bindings = sorted(
            (binding for row in values for binding in row.bindings),
            key=lambda binding: binding.evidence_id,
        )
        total_bindings = sum(row.total_binding_count for row in values)
        selected_bindings = tuple(all_bindings[:MAX_COMPARISON_BINDINGS])
        omitted_bindings = total_bindings - len(selected_bindings)
        if reasons:
            incomparable.append(
                IncomparableMetricDimension(
                    dimension=metric_dimension,
                    reasons=tuple(reasons),
                    present_entity_ids=tuple(
                        entity_id for entity_id in entity_ids if entity_id in present
                    ),
                    missing_entity_ids=missing,
                )
            )
        comparisons.append(
            CompetitiveMetricComparison(
                dimension=metric_dimension,
                values=tuple(
                    EntityMetricValue(
                        entity_id=row.subject_id,
                        observed_date=row.observed_date,
                        metric_period_start=row.metric_period_start,
                        metric_period_end=row.metric_period_end,
                        value=row.value,
                        reported_values=row.reported_values,
                        total_reported_value_count=row.total_reported_value_count,
                        omitted_reported_value_count=row.omitted_reported_value_count,
                        disputed=row.disputed,
                        period_start_disputed=row.period_start_disputed,
                    )
                    for row in values
                ),
                missing_entity_ids=missing,
                comparable=comparable,
                incomparability_reasons=tuple(reasons),
                bindings=selected_bindings,
                total_binding_count=total_bindings,
                omitted_binding_count=omitted_bindings,
            )
        )
    observed_dimensions = set(by_dimension)
    declared_missing = [
        dimension
        for dimension in data.scope.metric_dimensions
        if _dimension_key(dimension) not in observed_dimensions
    ]
    incomparable.extend(
        IncomparableMetricDimension(
            dimension=dimension,
            reasons=("no_matching_evidence",),
            present_entity_ids=(),
            missing_entity_ids=entity_ids,
        )
        for dimension in declared_missing
    )
    selected = comparisons[:MAX_OUTPUT_ROWS]
    selected_incomparable = incomparable[
        : max(0, MAX_OUTPUT_ROWS - len(selected))
    ]
    omitted = (len(comparisons) - len(selected)) + (
        len(incomparable) - len(selected_incomparable)
    )
    (
        _contradiction_rows,
        _contradiction_total,
        contradiction_summary,
        subject_contradiction_counts,
    ) = _contradictions(data.evidence, 0)
    subjects_with_evidence = {item.subject_id for item in data.evidence}
    missing_entities = tuple(
        entity_id for entity_id in entity_ids if entity_id not in subjects_with_evidence
    )
    profiles = _profiles(data, subject_contradiction_counts)
    source_bindings, manifest_sha256 = _analysis_provenance(
        data.metadata,
        data.scope,
        data.evidence,
        analysis_type="competitive_landscape_comparison",
        validated_input=data,
    )
    profile_metric_omissions = sum(
        profile.latest_metric_counts.omitted for profile in profiles
    )
    omitted += profile_metric_omissions
    profile_binding_omissions = sum(
        profile.omitted_binding_count
        + _reported_binding_omissions(profile.latest_metrics)
        for profile in profiles
    )
    binding_omissions = (
        _reported_binding_omissions(selected) + profile_binding_omissions
    )
    emitted_binding_count = sum(
        len(profile.bindings)
        + sum(len(metric.bindings) for metric in profile.latest_metrics)
        for profile in profiles
    ) + sum(len(row.bindings) for row in selected)
    if emitted_binding_count > MAX_COMPETITIVE_TOTAL_BINDINGS:
        raise RuntimeError("competitive output binding budget invariant exceeded")
    warnings = [
        "This deterministic comparison is not investment advice and provides no strategic recommendations.",
        "No rankings are generated; missing and incomparable metric-unit evidence is disclosed.",
        "Caller-provided text is inert data; embedded instructions are never executed.",
    ]
    if omitted:
        warnings.append(
            "Competitive output row caps were reached and omitted rows are reported."
        )
    if binding_omissions:
        warnings.append(
            "Displayed caller-provided evidence bindings reached per-output caps; omissions are reported."
        )
    return CompetitiveLandscapeAnalysis(
        source_citations=data.metadata.sources,
        source_bindings=source_bindings,
        analysis_manifest_sha256=manifest_sha256,
        scope=data.scope,
        entity_profiles=profiles,
        metric_comparisons=tuple(selected),
        incomparable_dimensions=tuple(selected_incomparable),
        missing_evidence_entity_ids=missing_entities,
        profile_counts=SectionCounts(
            total=len(data.entities),
            emitted=len(profiles),
            omitted=0,
        ),
        metric_comparison_counts=SectionCounts(
            total=len(comparisons),
            emitted=len(selected),
            omitted=len(comparisons) - len(selected),
        ),
        incomparable_dimension_counts=SectionCounts(
            total=len(incomparable),
            emitted=len(selected_incomparable),
            omitted=len(incomparable) - len(selected_incomparable),
        ),
        contradiction_summary=contradiction_summary,
        observation_recency=_observation_recency(
            data.evidence, data.metadata.as_of
        ),
        source_recency=_source_recency(data.evidence, data.metadata),
        complete=omitted == 0 and binding_omissions == 0,
        omitted_output_count=omitted,
        omitted_binding_count=binding_omissions,
        assumptions=(
            "observed_date is the caller-declared evidence observation or publication date; metric_period_start and metric_period_end describe metric timing.",
            "Entities are comparable only when every declared metric dimension, period start, period endpoint, and fiscal calendar matches.",
            "Source recency uses each unique referenced caller-declared source date once; no retrieval timestamp exists.",
            "Each entity contributes its latest non-authoritative observation set per complete metric dimension.",
            "Comparison order follows caller-declared entity order and does not imply rank.",
        ),
        limitations=(
            "No currency, unit, accounting, geographic, product, or time-period normalization is performed.",
            "Latest period endpoints may differ between entities; such rows are explicitly incomparable.",
            "No retrieval timestamp is available and no caller-declared source date is independently verified.",
            "Caller-declared source IDs, citations, and dates can be duplicated across records or spoofed and do not establish source independence.",
            "Sparse or contradictory evidence can make comparisons incomplete; no missing values are inferred.",
            "Analysis is transient and in-memory; this module performs no persistence, network, browser, model, or tool calls.",
        ),
        warnings=tuple(warnings),
    )


# Explicit aliases keep the public vocabulary discoverable without changing behavior.
ValueKind = MetricValueKind
Scale = MetricScale
PeriodType = MetricPeriodType
analyze_market_research = analyze_market_evidence
analyze_competitive_landscape = compare_competitive_landscape
