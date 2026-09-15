from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from grandice_domain import (
    AnalysisMetadata,
    CompanyAnalysisInput,
    FinancialPeriod,
    PortfolioAnalysisInput,
    PortfolioPosition,
    RiskAssessmentInput,
    RiskProfile,
    SourceMetadata,
    analyze_company,
    analyze_portfolio,
    assess_client_risk,
    calculate_company_metrics,
    calculate_portfolio_metrics,
    calculate_risk_score,
)


AS_OF = date(2026, 9, 14)


def metadata(*source_ids: str) -> AnalysisMetadata:
    return AnalysisMetadata(
        as_of=AS_OF,
        sources=tuple(
            SourceMetadata(
                source_id=source_id,
                citation=f"Caller-supplied record {source_id}",
                as_of=AS_OF,
            )
            for source_id in source_ids
        ),
    )


def test_portfolio_metrics_are_decimal_deterministic_and_structured() -> None:
    data = PortfolioAnalysisInput(
        metadata=metadata("positions"),
        reporting_currency="USD",
        positions=(
            PortfolioPosition(
                instrument_id="A",
                market_value=Decimal("50"),
                asset_class="Equity",
                sector="Technology",
                country="US",
                source_id="positions",
            ),
            PortfolioPosition(
                instrument_id="B",
                market_value=Decimal("30"),
                asset_class="Bond",
                sector="Government",
                country="US",
                source_id="positions",
            ),
            PortfolioPosition(
                instrument_id="C",
                market_value=Decimal("20"),
                asset_class="Equity",
                sector="Health",
                country="GB",
                source_id="positions",
            ),
        ),
    )

    metrics = calculate_portfolio_metrics(data)
    result = analyze_portfolio(data)

    assert metrics.total_market_value == Decimal("100")
    assert [item.weight for item in metrics.weights] == [
        Decimal("0.500000"),
        Decimal("0.300000"),
        Decimal("0.200000"),
    ]
    assert metrics.hhi == Decimal("0.380000")
    assert metrics.effective_positions == Decimal("2.631579")
    assert metrics.top_concentration.top_3_weight == Decimal("1.000000")
    assert {
        item.category: item.weight for item in metrics.allocation_by_asset_class
    } == {"Bond": Decimal("0.300000"), "Equity": Decimal("0.700000")}
    assert result.diversification_flags.single_position_above_25_pct is True
    assert result.professional_review_required is True
    assert result.methodology_version == "grandice-domain-1.0.0"


def test_risk_score_has_transparent_weighted_components_and_band() -> None:
    data = RiskAssessmentInput(
        metadata=metadata("questionnaire"),
        profile=RiskProfile(
            source_id="questionnaire",
            loss_tolerance=5,
            time_horizon=4,
            financial_stability=3,
            liquidity_flexibility=2,
            investment_knowledge=1,
        ),
    )

    metrics = calculate_risk_score(data)
    result = assess_client_risk(data)

    assert metrics.score == Decimal("62.500000")
    assert sum(
        (component.weight for component in metrics.components), Decimal("0")
    ) == Decimal("1.00")
    assert [component.weighted_contribution for component in metrics.components] == [
        Decimal("30.000000"),
        Decimal("18.750000"),
        Decimal("10.000000"),
        Decimal("3.750000"),
        Decimal("0.000000"),
    ]
    assert result.band.code == "moderate"
    assert result.professional_review_required is True


def test_company_metrics_include_supported_growth_leverage_and_valuation() -> None:
    data = CompanyAnalysisInput(
        metadata=metadata("fy2025", "fy2026"),
        company_id="issuer-1",
        reporting_currency="USD",
        periods=(
            FinancialPeriod(
                period_end=date(2025, 12, 31),
                source_id="fy2025",
                revenue=Decimal("100"),
                gross_profit=Decimal("40"),
                operating_income=Decimal("10"),
                net_income=Decimal("5"),
                ebitda=Decimal("15"),
                cash=Decimal("8"),
                total_debt=Decimal("38"),
            ),
            FinancialPeriod(
                period_end=date(2026, 6, 30),
                source_id="fy2026",
                revenue=Decimal("120"),
                gross_profit=Decimal("54"),
                operating_income=Decimal("18"),
                net_income=Decimal("9"),
                ebitda=Decimal("24"),
                cash=Decimal("10"),
                total_debt=Decimal("40"),
                diluted_shares=Decimal("10"),
                share_price=Decimal("15"),
            ),
        ),
    )

    metrics = calculate_company_metrics(data)
    result = analyze_company(data)

    assert metrics.growth[0].revenue_growth == Decimal("0.200000")
    assert metrics.growth[0].operating_income_growth == Decimal("0.800000")
    assert metrics.periods[-1].gross_margin == Decimal("0.450000")
    assert metrics.periods[-1].net_debt == Decimal("30")
    assert metrics.periods[-1].net_debt_to_ebitda == Decimal("1.250000")
    assert metrics.valuation is not None
    assert metrics.valuation.market_cap == Decimal("150")
    assert metrics.valuation.enterprise_value == Decimal("180")
    assert metrics.valuation.price_to_sales == Decimal("1.250000")
    assert metrics.valuation.price_to_earnings == Decimal("16.666667")
    assert metrics.valuation.enterprise_value_to_ebitda == Decimal("7.500000")
    assert [source.source_id for source in result.source_citations] == [
        "fy2025",
        "fy2026",
    ]
    assert result.professional_review_required is True


def test_company_omits_unsupported_valuation_and_zero_base_growth() -> None:
    data = CompanyAnalysisInput(
        metadata=metadata("filing"),
        company_id="issuer-2",
        reporting_currency="EUR",
        periods=(
            FinancialPeriod(
                period_end=date(2025, 12, 31),
                source_id="filing",
                revenue=Decimal("80"),
                operating_income=Decimal("0"),
                net_income=Decimal("-2"),
                cash=Decimal("5"),
                total_debt=Decimal("5"),
            ),
            FinancialPeriod(
                period_end=date(2026, 6, 30),
                source_id="filing",
                revenue=Decimal("100"),
                operating_income=Decimal("4"),
                net_income=Decimal("1"),
                cash=Decimal("6"),
                total_debt=Decimal("5"),
            ),
        ),
    )

    result = analyze_company(data)

    assert result.metrics.growth[0].operating_income_growth is None
    assert result.metrics.valuation is None
    assert result.flags.valuation_inputs_unavailable is True


def test_inputs_reject_floats_extra_fields_and_unknown_sources() -> None:
    with pytest.raises(ValidationError):
        PortfolioPosition(
            instrument_id="A",
            market_value=10.0,
            asset_class="Equity",
            sector="Technology",
            country="US",
            source_id="positions",
        )

    with pytest.raises(ValidationError, match="magnitude"):
        PortfolioPosition(
            instrument_id="A",
            market_value="1e40",
            asset_class="Equity",
            sector="Technology",
            country="US",
            source_id="positions",
        )

    with pytest.raises(ValidationError):
        RiskProfile(
            source_id="questionnaire",
            loss_tolerance=6,
            time_horizon=3,
            financial_stability=3,
            liquidity_flexibility=3,
            investment_knowledge=3,
            client_name="not allowed",
        )

    with pytest.raises(ValidationError, match="source_id"):
        PortfolioAnalysisInput(
            metadata=metadata("known"),
            reporting_currency="USD",
            positions=(
                PortfolioPosition(
                    instrument_id="A",
                    market_value=Decimal("10"),
                    asset_class="Equity",
                    sector="Technology",
                    country="US",
                    source_id="unknown",
                ),
            ),
        )


def test_portfolio_thresholds_use_unrounded_values() -> None:
    values = ("25.0000001", "24.9999999", "25", "25")
    data = PortfolioAnalysisInput(
        metadata=metadata("positions"),
        reporting_currency="USD",
        positions=tuple(
            PortfolioPosition(
                instrument_id=f"P{index}",
                market_value=value,
                asset_class=f"Class{index}",
                sector=f"Sector{index}",
                country="US",
                source_id="positions",
            )
            for index, value in enumerate(values)
        ),
    )

    result = analyze_portfolio(data)

    assert result.metrics.top_concentration.top_1_weight == Decimal("0.250000")
    assert result.diversification_flags.single_position_above_25_pct is True
