from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import (
    HUNDRED,
    AnalysisDisclosures,
    AnalysisMetadata,
    CategoryLabel,
    CurrencyCode,
    OpaqueId,
    PositiveMoney,
    SourceMetadata,
    StrictModel,
    ZERO,
    divide,
    quantize,
)


class PortfolioPosition(StrictModel):
    instrument_id: OpaqueId
    market_value: PositiveMoney
    asset_class: CategoryLabel
    sector: CategoryLabel
    country: CategoryLabel
    source_id: OpaqueId


class PortfolioAnalysisInput(StrictModel):
    metadata: AnalysisMetadata
    reporting_currency: CurrencyCode
    positions: tuple[PortfolioPosition, ...] = Field(
        min_length=1, max_length=5000
    )

    @model_validator(mode="after")
    def validate_sources(self) -> "PortfolioAnalysisInput":
        source_ids = [source.source_id for source in self.metadata.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("metadata source_id values must be unique")
        known_sources = set(source_ids)
        if any(position.source_id not in known_sources for position in self.positions):
            raise ValueError("every position source_id must reference metadata.sources")
        if any(source.as_of > self.metadata.as_of for source in self.metadata.sources):
            raise ValueError("source as_of cannot be later than analysis as_of")
        identifiers = [position.instrument_id for position in self.positions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("instrument_id values must be unique")
        return self


class PositionWeight(StrictModel):
    instrument_id: str
    market_value: Decimal
    weight: Decimal
    weight_pct: Decimal


class AllocationEntry(StrictModel):
    category: str
    market_value: Decimal
    weight: Decimal
    weight_pct: Decimal


class TopConcentration(StrictModel):
    top_1_weight: Decimal
    top_3_weight: Decimal
    top_5_weight: Decimal


class PortfolioMetrics(StrictModel):
    reporting_currency: str
    total_market_value: Decimal
    position_count: int
    weights: tuple[PositionWeight, ...]
    allocation_by_asset_class: tuple[AllocationEntry, ...]
    allocation_by_sector: tuple[AllocationEntry, ...]
    allocation_by_country: tuple[AllocationEntry, ...]
    hhi: Decimal
    effective_positions: Decimal
    top_concentration: TopConcentration


class DiversificationFlags(StrictModel):
    fewer_than_five_positions: bool
    single_position_above_25_pct: bool
    hhi_above_0_18: bool
    single_asset_class_above_80_pct: bool


class PortfolioAnalysis(AnalysisDisclosures):
    analysis_type: Literal["portfolio"] = "portfolio"
    as_of: date
    source_citations: tuple[SourceMetadata, ...]
    metrics: PortfolioMetrics
    diversification_flags: DiversificationFlags


def _allocation(
    positions: tuple[PortfolioPosition, ...], attribute: str, total: Decimal
) -> tuple[AllocationEntry, ...]:
    amounts: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for position in positions:
        amounts[getattr(position, attribute)] += position.market_value
    return tuple(
        AllocationEntry(
            category=category,
            market_value=amount,
            weight=quantize(divide(amount, total)),
            weight_pct=quantize(divide(amount, total) * HUNDRED),
        )
        for category, amount in sorted(amounts.items())
    )


def calculate_portfolio_metrics(data: PortfolioAnalysisInput) -> PortfolioMetrics:
    total = sum((position.market_value for position in data.positions), ZERO)
    ordered = sorted(
        data.positions, key=lambda position: (-position.market_value, position.instrument_id)
    )
    raw_weights = [divide(position.market_value, total) for position in ordered]
    weights = tuple(
        PositionWeight(
            instrument_id=position.instrument_id,
            market_value=position.market_value,
            weight=quantize(weight),
            weight_pct=quantize(weight * HUNDRED),
        )
        for position, weight in zip(ordered, raw_weights, strict=True)
    )
    hhi = sum((weight * weight for weight in raw_weights), ZERO)

    def top_weight(count: int) -> Decimal:
        return quantize(sum(raw_weights[:count], ZERO))

    return PortfolioMetrics(
        reporting_currency=data.reporting_currency,
        total_market_value=total,
        position_count=len(data.positions),
        weights=weights,
        allocation_by_asset_class=_allocation(data.positions, "asset_class", total),
        allocation_by_sector=_allocation(data.positions, "sector", total),
        allocation_by_country=_allocation(data.positions, "country", total),
        hhi=quantize(hhi),
        effective_positions=quantize(divide(Decimal("1"), hhi)),
        top_concentration=TopConcentration(
            top_1_weight=top_weight(1),
            top_3_weight=top_weight(3),
            top_5_weight=top_weight(5),
        ),
    )


def interpret_portfolio_metrics(metrics: PortfolioMetrics) -> DiversificationFlags:
    exact_weights = tuple(
        divide(item.market_value, metrics.total_market_value)
        for item in metrics.weights
    )
    largest_asset_class = max(
        (
            divide(item.market_value, metrics.total_market_value)
            for item in metrics.allocation_by_asset_class
        ),
        default=ZERO,
    )
    exact_hhi = sum((weight * weight for weight in exact_weights), ZERO)
    return DiversificationFlags(
        fewer_than_five_positions=metrics.position_count < 5,
        single_position_above_25_pct=max(exact_weights) > Decimal("0.25"),
        hhi_above_0_18=exact_hhi > Decimal("0.18"),
        single_asset_class_above_80_pct=largest_asset_class > Decimal("0.80"),
    )


def analyze_portfolio(data: PortfolioAnalysisInput) -> PortfolioAnalysis:
    metrics = calculate_portfolio_metrics(data)
    flags = interpret_portfolio_metrics(metrics)
    warnings = ["Concentration flags are screening indicators, not investment recommendations."]
    if any(
        (
            flags.fewer_than_five_positions,
            flags.single_position_above_25_pct,
            flags.hhi_above_0_18,
            flags.single_asset_class_above_80_pct,
        )
    ):
        warnings.append("One or more rule-based diversification flags were triggered.")
    return PortfolioAnalysis(
        as_of=data.metadata.as_of,
        source_citations=data.metadata.sources,
        metrics=metrics,
        diversification_flags=flags,
        assumptions=(
            "Market values are positive, comparable, and expressed in the reporting currency.",
            "HHI uses decimal position weights; effective positions equals 1 / HHI.",
            "Screening thresholds are 25% single-position, 0.18 HHI, and 80% asset-class.",
        ),
        limitations=(
            "Does not model correlation, volatility, liquidity, taxes, fees, or look-through exposures.",
            "Classification quality depends entirely on caller-provided categories and market values.",
        ),
        warnings=tuple(warnings),
    )
