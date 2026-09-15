from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from .company import CompanyAnalysisInput, analyze_company
from .portfolio import PortfolioAnalysisInput, analyze_portfolio
from .risk import RiskAssessmentInput, assess_client_risk


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    domain: str
    name: str
    description: str
    input_model: type[BaseModel]
    analyzer: Callable[[Any], BaseModel]
    tools: tuple[str, ...]
    privacy_policy: str
    source_grounded: bool = True
    deterministic: bool = True
    professional_review_required: bool = True

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.__name__,
            "tools": list(self.tools),
            "privacy_policy": self.privacy_policy,
            "source_grounded": self.source_grounded,
            "deterministic": self.deterministic,
            "professional_review_required": self.professional_review_required,
        }


AGENTS: dict[str, AgentDefinition] = {
    "financial.portfolio-analyst.v1": AgentDefinition(
        id="financial.portfolio-analyst.v1",
        domain="financial",
        name="Portfolio Analysis Agent",
        description=(
            "Calculates portfolio weights, allocation, concentration, HHI, "
            "effective positions, and transparent diversification flags."
        ),
        input_model=PortfolioAnalysisInput,
        analyzer=analyze_portfolio,
        tools=(
            "decimal_portfolio_math",
            "allocation_aggregator",
            "concentration_rules",
        ),
        privacy_policy="financial-strict-v1",
    ),
    "financial.risk-assessment.v1": AgentDefinition(
        id="financial.risk-assessment.v1",
        domain="financial",
        name="Risk Assessment Agent",
        description=(
            "Applies a disclosed weighted scoring methodology to bounded, "
            "non-identifying client profile dimensions."
        ),
        input_model=RiskAssessmentInput,
        analyzer=assess_client_risk,
        tools=("weighted_risk_score", "risk_band_rules"),
        privacy_policy="financial-strict-v1",
    ),
    "financial.company-analyst.v1": AgentDefinition(
        id="financial.company-analyst.v1",
        domain="financial",
        name="Company Financial Analysis Agent",
        description=(
            "Calculates historical growth, margins, leverage, and supported "
            "valuation multiples from caller-cited financial periods."
        ),
        input_model=CompanyAnalysisInput,
        analyzer=analyze_company,
        tools=(
            "financial_statement_ratios",
            "growth_calculator",
            "valuation_arithmetic",
        ),
        privacy_policy="financial-strict-v1",
    ),
}


def get_agent(agent_id: str) -> AgentDefinition:
    try:
        return AGENTS[agent_id]
    except KeyError as exc:
        raise ValueError(f"Unknown agent '{agent_id}'.") from exc

