from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import (
    AnalysisDisclosures,
    AnalysisMetadata,
    CurrencyCode,
    Money,
    NonNegativeMoney,
    OpaqueId,
    PositiveMoney,
    SourceMetadata,
    StrictModel,
    ZERO,
    divide,
    quantize,
)


class FinancialPeriod(StrictModel):
    period_end: date
    source_id: OpaqueId
    revenue: PositiveMoney
    gross_profit: Money | None = None
    operating_income: Money
    net_income: Money
    ebitda: Money | None = None
    cash: NonNegativeMoney
    total_debt: NonNegativeMoney
    diluted_shares: PositiveMoney | None = None
    share_price: NonNegativeMoney | None = None

    @model_validator(mode="after")
    def validate_valuation_pair(self) -> "FinancialPeriod":
        if (self.diluted_shares is None) != (self.share_price is None):
            raise ValueError("diluted_shares and share_price must be provided together")
        return self


class CompanyAnalysisInput(StrictModel):
    metadata: AnalysisMetadata
    company_id: OpaqueId
    reporting_currency: CurrencyCode
    periods: tuple[FinancialPeriod, ...] = Field(
        min_length=2, max_length=100
    )

    @model_validator(mode="after")
    def validate_periods_and_sources(self) -> "CompanyAnalysisInput":
        source_ids = [source.source_id for source in self.metadata.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("metadata source_id values must be unique")
        known_sources = set(source_ids)
        if any(period.source_id not in known_sources for period in self.periods):
            raise ValueError("every period source_id must reference metadata.sources")
        if any(source.as_of > self.metadata.as_of for source in self.metadata.sources):
            raise ValueError("source as_of cannot be later than analysis as_of")
        period_ends = [period.period_end for period in self.periods]
        if len(period_ends) != len(set(period_ends)):
            raise ValueError("period_end values must be unique")
        if any(period_end > self.metadata.as_of for period_end in period_ends):
            raise ValueError("period_end cannot be later than analysis as_of")
        source_dates = {
            source.source_id: source.as_of for source in self.metadata.sources
        }
        if any(
            source_dates[period.source_id] < period.period_end
            for period in self.periods
        ):
            raise ValueError(
                "a period source as_of cannot be earlier than its period_end"
            )
        return self


class CompanyPeriodMetrics(StrictModel):
    period_end: date
    revenue: Decimal
    gross_margin: Decimal | None
    operating_margin: Decimal
    net_margin: Decimal
    ebitda_margin: Decimal | None
    net_debt: Decimal
    net_debt_to_ebitda: Decimal | None


class CompanyGrowthMetrics(StrictModel):
    from_period_end: date
    to_period_end: date
    revenue_growth: Decimal
    operating_income_growth: Decimal | None
    net_income_growth: Decimal | None
    ebitda_growth: Decimal | None


class ValuationMetrics(StrictModel):
    period_end: date
    market_cap: Decimal
    enterprise_value: Decimal
    price_to_sales: Decimal
    price_to_earnings: Decimal | None
    enterprise_value_to_ebitda: Decimal | None


class CompanyMetrics(StrictModel):
    company_id: str
    reporting_currency: str
    periods: tuple[CompanyPeriodMetrics, ...]
    growth: tuple[CompanyGrowthMetrics, ...]
    valuation: ValuationMetrics | None


class CompanyAnalysisFlags(StrictModel):
    latest_net_income_negative: bool
    latest_ebitda_non_positive: bool | None
    latest_net_debt_positive: bool
    valuation_inputs_unavailable: bool


class CompanyAnalysis(AnalysisDisclosures):
    analysis_type: Literal["company_financials"] = "company_financials"
    as_of: date
    source_citations: tuple[SourceMetadata, ...]
    metrics: CompanyMetrics
    flags: CompanyAnalysisFlags


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    return quantize(divide(numerator, denominator))


def _growth(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == ZERO:
        return None
    return _ratio(current - previous, abs(previous))


def calculate_company_metrics(data: CompanyAnalysisInput) -> CompanyMetrics:
    periods = sorted(data.periods, key=lambda period: period.period_end)
    period_metrics = tuple(
        CompanyPeriodMetrics(
            period_end=period.period_end,
            revenue=period.revenue,
            gross_margin=(
                _ratio(period.gross_profit, period.revenue)
                if period.gross_profit is not None
                else None
            ),
            operating_margin=_ratio(period.operating_income, period.revenue),
            net_margin=_ratio(period.net_income, period.revenue),
            ebitda_margin=(
                _ratio(period.ebitda, period.revenue) if period.ebitda is not None else None
            ),
            net_debt=period.total_debt - period.cash,
            net_debt_to_ebitda=(
                _ratio(period.total_debt - period.cash, period.ebitda)
                if period.ebitda is not None and period.ebitda > ZERO
                else None
            ),
        )
        for period in periods
    )
    growth = tuple(
        CompanyGrowthMetrics(
            from_period_end=previous.period_end,
            to_period_end=current.period_end,
            revenue_growth=_growth(current.revenue, previous.revenue),
            operating_income_growth=_growth(
                current.operating_income, previous.operating_income
            ),
            net_income_growth=_growth(current.net_income, previous.net_income),
            ebitda_growth=(
                _growth(current.ebitda, previous.ebitda)
                if current.ebitda is not None and previous.ebitda is not None
                else None
            ),
        )
        for previous, current in zip(periods, periods[1:])
    )
    latest = periods[-1]
    valuation = None
    if latest.diluted_shares is not None and latest.share_price is not None:
        market_cap = latest.diluted_shares * latest.share_price
        enterprise_value = market_cap + latest.total_debt - latest.cash
        valuation = ValuationMetrics(
            period_end=latest.period_end,
            market_cap=market_cap,
            enterprise_value=enterprise_value,
            price_to_sales=_ratio(market_cap, latest.revenue),
            price_to_earnings=(
                _ratio(market_cap, latest.net_income) if latest.net_income > ZERO else None
            ),
            enterprise_value_to_ebitda=(
                _ratio(enterprise_value, latest.ebitda)
                if latest.ebitda is not None and latest.ebitda > ZERO
                else None
            ),
        )
    return CompanyMetrics(
        company_id=data.company_id,
        reporting_currency=data.reporting_currency,
        periods=period_metrics,
        growth=growth,
        valuation=valuation,
    )


def interpret_company_metrics(
    data: CompanyAnalysisInput, metrics: CompanyMetrics
) -> CompanyAnalysisFlags:
    latest_input = max(data.periods, key=lambda period: period.period_end)
    latest_metrics = metrics.periods[-1]
    return CompanyAnalysisFlags(
        latest_net_income_negative=latest_input.net_income < ZERO,
        latest_ebitda_non_positive=(
            latest_input.ebitda <= ZERO if latest_input.ebitda is not None else None
        ),
        latest_net_debt_positive=latest_metrics.net_debt > ZERO,
        valuation_inputs_unavailable=metrics.valuation is None,
    )


def analyze_company(data: CompanyAnalysisInput) -> CompanyAnalysis:
    metrics = calculate_company_metrics(data)
    flags = interpret_company_metrics(data, metrics)
    warnings = [
        "This historical ratio analysis is not a valuation opinion or a buy/sell recommendation."
    ]
    if flags.latest_net_income_negative:
        warnings.append("Latest net income is negative; P/E is not reported.")
    if flags.latest_ebitda_non_positive:
        warnings.append("Latest EBITDA is non-positive; EBITDA-based ratios are not reported.")
    if flags.valuation_inputs_unavailable:
        warnings.append("Latest share count and price were not supplied; valuation is not reported.")
    return CompanyAnalysis(
        as_of=data.metadata.as_of,
        source_citations=data.metadata.sources,
        metrics=metrics,
        flags=flags,
        assumptions=(
            "All monetary period values use the stated reporting currency and comparable accounting definitions.",
            "Growth equals (current - prior) / abs(prior); it is omitted when prior equals zero.",
            "Margins divide the stated profit measure by revenue; net debt equals debt minus cash.",
            "Market cap equals diluted shares times share price; enterprise value adds net debt.",
        ),
        limitations=(
            "No normalization for accounting policy changes, restatements, seasonality, dilution, or one-time items.",
            "Valuation multiples are point-in-time arithmetic and omit forecasts, peer context, and market conditions.",
            "Source accuracy and period comparability are the caller's responsibility.",
        ),
        warnings=tuple(warnings),
    )
