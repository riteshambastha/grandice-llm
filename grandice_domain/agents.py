from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from .company import CompanyAnalysisInput, analyze_company
from .legal import (
    ContractAnalysisInput,
    ContractComparisonInput,
    analyze_contract,
    compare_contracts,
)
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
    capabilities: tuple[str, ...]
    privacy_policy: str
    execution_timeout_seconds: float = 5.0
    caller_source_attributed: bool = True
    deterministic: bool = True
    professional_review_required: bool = True

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.__name__,
            "capabilities": list(self.capabilities),
            "privacy_policy": self.privacy_policy,
            "execution_timeout_seconds": self.execution_timeout_seconds,
            "caller_source_attributed": self.caller_source_attributed,
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
        capabilities=(
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
        capabilities=("weighted_risk_score", "risk_band_rules"),
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
        capabilities=(
            "financial_statement_ratios",
            "growth_calculator",
            "valuation_arithmetic",
        ),
        privacy_policy="financial-strict-v1",
    ),
    "legal.contract-reviewer.v1": AgentDefinition(
        id="legal.contract-reviewer.v1",
        domain="legal",
        name="Contract Review Agent",
        description=(
            "Segments commercial contracts, classifies clauses, applies fixed "
            "risk rules and a bounded literal-only review playbook."
        ),
        input_model=ContractAnalysisInput,
        analyzer=analyze_contract,
        capabilities=(
            "deterministic_clause_segmenter",
            "legal_clause_taxonomy",
            "literal_playbook_rules",
            "source_span_hasher",
        ),
        privacy_policy="legal-strict-v1",
        execution_timeout_seconds=5.0,
    ),
    "legal.contract-comparator.v1": AgentDefinition(
        id="legal.contract-comparator.v1",
        domain="legal",
        name="Contract Comparison Agent",
        description=(
            "Compares original-to-revised clauses with bounded deterministic "
            "candidate matching and reports explainable risk deltas."
        ),
        input_model=ContractComparisonInput,
        analyzer=compare_contracts,
        capabilities=(
            "typed_clause_alignment",
            "bounded_text_similarity",
            "risk_delta_calculator",
            "source_span_hasher",
        ),
        privacy_policy="legal-strict-v1",
        execution_timeout_seconds=10.0,
    ),
}


def get_agent(agent_id: str) -> AgentDefinition:
    try:
        return AGENTS[agent_id]
    except KeyError as exc:
        raise ValueError("Unknown agent.") from exc

