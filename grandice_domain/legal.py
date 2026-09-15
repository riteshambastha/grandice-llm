from __future__ import annotations

import hashlib
import re
from collections import Counter
from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from .common import (
    AnalysisDisclosures,
    AnalysisMetadata,
    OpaqueId,
    SourceMetadata,
    StrictModel,
)


LEGAL_METHODOLOGY_VERSION = "grandice-legal-deterministic-1.0.0"
MAX_DOCUMENT_CHARS = 200_000
MAX_SEGMENTS = 250
MAX_FINDINGS = 250
MAX_COMPARISONS = 250
MAX_EXCERPT_CHARS = 320
MAX_CANDIDATES_PER_CLAUSE = 16

BoundedText = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_DOCUMENT_CHARS)
]
BoundedPhrase = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
BoundedLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
]


class DocumentType(str, Enum):
    MASTER_SERVICES_AGREEMENT = "master_services_agreement"
    SOFTWARE_LICENSE = "software_license"
    SAAS = "saas"
    DATA_PROCESSING_AGREEMENT = "data_processing_agreement"
    NON_DISCLOSURE_AGREEMENT = "non_disclosure_agreement"
    PURCHASE = "purchase"
    SUPPLY = "supply"
    DISTRIBUTION = "distribution"
    EMPLOYMENT = "employment"
    CONSULTING = "consulting"
    LEASE = "lease"
    PARTNERSHIP = "partnership"
    OTHER_COMMERCIAL = "other_commercial"


class ClauseType(str, Enum):
    PREAMBLE = "preamble"
    DEFINITIONS = "definitions"
    PARTIES = "parties"
    SCOPE = "scope"
    SERVICES = "services"
    SERVICE_LEVELS = "service_levels"
    FEES = "fees"
    PAYMENT = "payment"
    TAXES = "taxes"
    TERM = "term"
    TERMINATION = "termination"
    RENEWAL = "renewal"
    WARRANTIES = "warranties"
    LIABILITY = "liability"
    INDEMNITY = "indemnity"
    INSURANCE = "insurance"
    CONFIDENTIALITY = "confidentiality"
    DATA_PROTECTION = "data_protection"
    INFORMATION_SECURITY = "information_security"
    INTELLECTUAL_PROPERTY = "intellectual_property"
    LICENSE = "license"
    ASSIGNMENT = "assignment"
    SUBCONTRACTING = "subcontracting"
    COMPLIANCE = "compliance"
    AUDIT = "audit"
    RECORDS = "records"
    GOVERNING_LAW = "governing_law"
    DISPUTE_RESOLUTION = "dispute_resolution"
    FORCE_MAJEURE = "force_majeure"
    NOTICES = "notices"
    CHANGE_CONTROL = "change_control"
    NON_SOLICITATION = "non_solicitation"
    PUBLICITY = "publicity"
    ENTIRE_AGREEMENT = "entire_agreement"
    AMENDMENT = "amendment"
    ORDER_OF_PRECEDENCE = "order_of_precedence"
    SEVERABILITY = "severability"
    WAIVER = "waiver"
    SURVIVAL = "survival"
    COUNTERPARTS = "counterparts"
    MISCELLANEOUS = "miscellaneous"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ContractDocument(StrictModel):
    document_id: OpaqueId
    source_id: OpaqueId
    text: BoundedText
    document_type: DocumentType
    jurisdiction: BoundedLabel | None = None


class ReviewPolicy(StrictModel):
    policy_id: OpaqueId
    title: BoundedLabel
    clause_types: tuple[ClauseType, ...] = Field(
        default_factory=tuple, max_length=20
    )
    literal_phrases: tuple[BoundedPhrase, ...] = Field(
        default_factory=tuple, max_length=20
    )
    severity: Severity = Severity.MEDIUM
    explanation: BoundedLabel

    @field_validator("literal_phrases")
    @classmethod
    def reject_empty_or_duplicate_phrases(
        cls, phrases: tuple[str, ...]
    ) -> tuple[str, ...]:
        folded = [phrase.casefold() for phrase in phrases]
        if len(folded) != len(set(folded)):
            raise ValueError("literal_phrases must be unique ignoring case")
        return phrases


class ContractPlaybook(StrictModel):
    playbook_id: OpaqueId
    version: OpaqueId
    required_clause_types: tuple[ClauseType, ...] = Field(
        default_factory=tuple, max_length=40
    )
    prohibited_literal_phrases: tuple[BoundedPhrase, ...] = Field(
        default_factory=tuple, max_length=50
    )
    preferred_governing_law: BoundedLabel | None = None
    liability_cap_required: bool = False
    review_policies: tuple[ReviewPolicy, ...] = Field(
        default_factory=tuple, max_length=50
    )

    @model_validator(mode="after")
    def validate_uniqueness(self) -> "ContractPlaybook":
        if len(self.required_clause_types) != len(set(self.required_clause_types)):
            raise ValueError("required_clause_types must be unique")
        phrases = [value.casefold() for value in self.prohibited_literal_phrases]
        if len(phrases) != len(set(phrases)):
            raise ValueError(
                "prohibited_literal_phrases must be unique ignoring case"
            )
        policy_ids = [policy.policy_id for policy in self.review_policies]
        if len(policy_ids) != len(set(policy_ids)):
            raise ValueError("review policy IDs must be unique")
        return self


class ContractAnalysisInput(StrictModel):
    metadata: AnalysisMetadata
    document: ContractDocument
    playbook: ContractPlaybook | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "ContractAnalysisInput":
        _validate_metadata(self.metadata)
        if self.document.source_id not in {
            source.source_id for source in self.metadata.sources
        }:
            raise ValueError("document source_id must reference metadata.sources")
        return self


class MethodEvidence(StrictModel):
    method: Literal[
        "heading_keyword",
        "body_keyword",
        "structural_fallback",
        "literal_phrase",
        "absence_check",
        "comparison",
    ]
    confidence: float = Field(strict=True, ge=0, le=1)
    matched_terms: tuple[BoundedPhrase, ...] = Field(
        default_factory=tuple, max_length=12
    )
    explanation: BoundedLabel


class ClauseSegment(StrictModel):
    clause_id: OpaqueId
    document_id: OpaqueId
    source_id: OpaqueId
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_unit: Literal["unicode_codepoints_v1"] = "unicode_codepoints_v1"
    clause_type: ClauseType
    heading: BoundedLabel | None
    char_start: int = Field(strict=True, ge=0, le=MAX_DOCUMENT_CHARS)
    char_end: int = Field(strict=True, ge=1, le=MAX_DOCUMENT_CHARS)
    line_start: int = Field(strict=True, ge=1)
    line_end: int = Field(strict=True, ge=1)
    excerpt: str = Field(min_length=1, max_length=MAX_EXCERPT_CHARS)
    span_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    excerpt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: MethodEvidence

    @model_validator(mode="after")
    def validate_span(self) -> "ClauseSegment":
        if self.char_end <= self.char_start:
            raise ValueError("char_end must be greater than char_start")
        if self.line_end < self.line_start:
            raise ValueError("line_end must not precede line_start")
        return self


class LegalFinding(StrictModel):
    risk_id: OpaqueId
    rule_id: OpaqueId
    finding_type: Literal["risk", "playbook", "policy"]
    severity: Severity
    title: BoundedLabel
    explanation: str = Field(min_length=1, max_length=700)
    clause_type: ClauseType | None = None
    clause_id: OpaqueId | None = None
    document_id: OpaqueId | None = None
    document_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    coordinate_unit: Literal["unicode_codepoints_v1"] = "unicode_codepoints_v1"
    char_start: int | None = Field(default=None, ge=0, le=MAX_DOCUMENT_CHARS)
    char_end: int | None = Field(default=None, ge=1, le=MAX_DOCUMENT_CHARS)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    excerpt: str | None = Field(default=None, max_length=MAX_EXCERPT_CHARS)
    span_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    excerpt_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    evidence: MethodEvidence


class ContractAnalysis(AnalysisDisclosures):
    methodology_version: str = LEGAL_METHODOLOGY_VERSION
    analysis_type: Literal["legal_contract_analysis"] = "legal_contract_analysis"
    source_citations: tuple[SourceMetadata, ...] = Field(
        min_length=1, max_length=100
    )
    document_id: OpaqueId
    source_id: OpaqueId
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete: bool
    omitted_clause_count: int = Field(strict=True, ge=0)
    omitted_finding_count: int = Field(strict=True, ge=0)
    clauses: tuple[ClauseSegment, ...] = Field(max_length=MAX_SEGMENTS)
    findings: tuple[LegalFinding, ...] = Field(max_length=MAX_FINDINGS)
    legal_advice: Literal[False] = False
    content_persisted: Literal[False] = False


class ContractComparisonInput(StrictModel):
    metadata: AnalysisMetadata
    original: ContractDocument
    revised: ContractDocument
    playbook: ContractPlaybook | None = None

    @model_validator(mode="after")
    def validate_sources(self) -> "ContractComparisonInput":
        _validate_metadata(self.metadata)
        known = {source.source_id for source in self.metadata.sources}
        if self.original.source_id not in known or self.revised.source_id not in known:
            raise ValueError("both document source_id values must reference metadata.sources")
        if self.original.document_id == self.revised.document_id:
            raise ValueError("original and revised document IDs must differ")
        return self


class ClauseChange(StrictModel):
    change_id: OpaqueId
    clause_type: ClauseType
    status: Literal["added", "removed", "modified", "unchanged", "ambiguous"]
    match_basis: Literal[
        "exact_hash",
        "category_token_similarity",
        "ambiguous_category_similarity",
        "unmatched_category",
    ]
    ambiguous_candidate_count: int = Field(strict=True, ge=0)
    original_clause_id: OpaqueId | None = None
    revised_clause_id: OpaqueId | None = None
    original_source_id: OpaqueId | None = None
    revised_source_id: OpaqueId | None = None
    original_document_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    revised_document_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    original_char_start: int | None = Field(default=None, ge=0)
    original_char_end: int | None = Field(default=None, ge=1)
    revised_char_start: int | None = Field(default=None, ge=0)
    revised_char_end: int | None = Field(default=None, ge=1)
    coordinate_unit: Literal["unicode_codepoints_v1"] = "unicode_codepoints_v1"
    original_span_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    revised_span_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    similarity: float = Field(strict=True, ge=0, le=1)
    original_excerpt: str | None = Field(default=None, max_length=MAX_EXCERPT_CHARS)
    revised_excerpt: str | None = Field(default=None, max_length=MAX_EXCERPT_CHARS)
    evidence: MethodEvidence


class RiskDelta(StrictModel):
    new_risk_ids: tuple[OpaqueId, ...] = Field(max_length=MAX_FINDINGS)
    resolved_risk_ids: tuple[OpaqueId, ...] = Field(max_length=MAX_FINDINGS)
    persistent_risk_ids: tuple[OpaqueId, ...] = Field(max_length=MAX_FINDINGS)


class ContractComparison(AnalysisDisclosures):
    methodology_version: str = LEGAL_METHODOLOGY_VERSION
    analysis_type: Literal["legal_contract_comparison"] = (
        "legal_contract_comparison"
    )
    source_citations: tuple[SourceMetadata, ...] = Field(
        min_length=1, max_length=100
    )
    original_document_id: OpaqueId
    revised_document_id: OpaqueId
    original_source_id: OpaqueId
    revised_source_id: OpaqueId
    original_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    revised_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    direction: Literal["original_to_revised"] = "original_to_revised"
    complete: bool
    omitted_change_count: int = Field(strict=True, ge=0)
    original_omitted_clause_count: int = Field(strict=True, ge=0)
    revised_omitted_clause_count: int = Field(strict=True, ge=0)
    original_omitted_finding_count: int = Field(strict=True, ge=0)
    revised_omitted_finding_count: int = Field(strict=True, ge=0)
    changes: tuple[ClauseChange, ...] = Field(max_length=MAX_COMPARISONS)
    risk_delta: RiskDelta
    legal_advice: Literal[False] = False
    content_persisted: Literal[False] = False


_TAXONOMY: tuple[tuple[ClauseType, tuple[str, ...]], ...] = (
    (ClauseType.DATA_PROTECTION, ("data protection", "personal data", "privacy")),
    (ClauseType.INFORMATION_SECURITY, ("information security", "security measures", "cybersecurity")),
    (ClauseType.SERVICE_LEVELS, ("service level", "sla", "uptime")),
    (ClauseType.INTELLECTUAL_PROPERTY, ("intellectual property", "ownership of work", "proprietary rights")),
    (ClauseType.GOVERNING_LAW, ("governing law", "laws of", "governed by")),
    (ClauseType.DISPUTE_RESOLUTION, ("dispute resolution", "arbitration", "venue", "jurisdiction")),
    (ClauseType.FORCE_MAJEURE, ("force majeure", "events beyond", "acts of god")),
    (ClauseType.ENTIRE_AGREEMENT, ("entire agreement", "whole agreement")),
    (ClauseType.ORDER_OF_PRECEDENCE, ("order of precedence", "conflict between")),
    (ClauseType.NON_SOLICITATION, ("non-solicitation", "non solicitation", "solicit employees")),
    (ClauseType.CHANGE_CONTROL, ("change control", "change order")),
    (ClauseType.CONFIDENTIALITY, ("confidentiality", "confidential information", "non-disclosure")),
    (ClauseType.SUBCONTRACTING, ("subcontract", "subprocessor")),
    (ClauseType.ASSIGNMENT, ("assignment", "assign this agreement")),
    (ClauseType.INDEMNITY, ("indemnity", "indemnification", "indemnify", "hold harmless")),
    (ClauseType.LIABILITY, ("limitation of liability", "liability", "damages")),
    (ClauseType.TERMINATION, ("termination", "terminate")),
    (ClauseType.RENEWAL, ("renewal", "automatically renew", "auto-renew")),
    (ClauseType.WARRANTIES, ("warranties", "warranty", "as is")),
    (ClauseType.INSURANCE, ("insurance", "coverage")),
    (ClauseType.DEFINITIONS, ("definitions", "defined terms")),
    (ClauseType.PARTIES, ("parties", "between")),
    (ClauseType.SERVICES, ("services", "statement of work", "deliverables")),
    (ClauseType.SCOPE, ("scope", "purpose")),
    (ClauseType.FEES, ("fees", "charges", "pricing")),
    (ClauseType.PAYMENT, ("payment", "invoice", "late fee")),
    (ClauseType.TAXES, ("taxes", "tax")),
    (ClauseType.TERM, ("term", "effective date")),
    (ClauseType.LICENSE, ("license", "licence", "usage rights")),
    (ClauseType.COMPLIANCE, ("compliance", "applicable laws", "anti-bribery")),
    (ClauseType.AUDIT, ("audit", "inspection rights")),
    (ClauseType.RECORDS, ("records", "record retention")),
    (ClauseType.NOTICES, ("notices", "notice shall")),
    (ClauseType.PUBLICITY, ("publicity", "press release", "use of name")),
    (ClauseType.AMENDMENT, ("amendment", "modification")),
    (ClauseType.SEVERABILITY, ("severability", "invalid provision")),
    (ClauseType.WAIVER, ("waiver", "failure to enforce")),
    (ClauseType.SURVIVAL, ("survival", "survive termination")),
    (ClauseType.COUNTERPARTS, ("counterparts", "electronic signature")),
    (ClauseType.MISCELLANEOUS, ("miscellaneous", "general provisions")),
)

_HEADING_RE = re.compile(
    r"^\s*(?:(?:section|article|schedule|exhibit)\s+)?"
    r"(?:\d+(?:\.\d+)*[.)]?\s+|[A-Z][.)]\s+)?"
    r"([A-Za-z][A-Za-z0-9 &/,'’().\-]{1,100}?)(?:\s*[:.])?\s*$",
    re.IGNORECASE,
)


def _validate_metadata(metadata: AnalysisMetadata) -> None:
    source_ids = [source.source_id for source in metadata.sources]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("metadata source_id values must be unique")
    if any(source.as_of > metadata.as_of for source in metadata.sources):
        raise ValueError("source as_of cannot be later than analysis as_of")


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _excerpt(text: str) -> str:
    compact = " ".join(text.split())
    return compact[:MAX_EXCERPT_CHARS]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _derived_id(prefix: str, *parts: object) -> str:
    readable = ":".join(str(part) for part in parts)
    candidate = f"{prefix}:{readable}"
    if len(candidate) <= 80:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _classify(heading: str | None, body: str) -> tuple[ClauseType, MethodEvidence]:
    heading_folded = (heading or "").casefold()
    body_folded = body.casefold()
    if heading_folded.endswith("agreement") and len(heading_folded.split()) <= 8:
        return ClauseType.PREAMBLE, MethodEvidence(
            method="structural_fallback",
            confidence=0.9,
            matched_terms=("agreement",),
            explanation="A short agreement title was classified as the preamble.",
        )
    for clause_type, terms in _TAXONOMY:
        matches = tuple(term for term in terms if term in heading_folded)
        if matches:
            return clause_type, MethodEvidence(
                method="heading_keyword",
                confidence=0.96,
                matched_terms=matches[:12],
                explanation="Clause category matched deterministic heading keywords.",
            )
    for clause_type, terms in _TAXONOMY:
        matches = tuple(term for term in terms if term in body_folded)
        if matches:
            return clause_type, MethodEvidence(
                method="body_keyword",
                confidence=0.78,
                matched_terms=matches[:12],
                explanation="Clause category matched deterministic body keywords.",
            )
    return ClauseType.UNKNOWN, MethodEvidence(
        method="structural_fallback",
        confidence=0.25,
        explanation="No taxonomy keyword matched; category is unknown.",
    )


def _looks_like_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 110:
        return None
    match = _HEADING_RE.match(line)
    if not match:
        return None
    label = match.group(1).strip(" .:")
    folded = label.casefold()
    taxonomy_hit = any(
        term in folded for _clause_type, terms in _TAXONOMY for term in terms
    )
    numbered = bool(
        re.match(
            r"^\s*(?:(?:section|article)\s+)?(?:\d+(?:\.\d+)*[.)]?|[A-Z][.)])\s+",
            line,
            re.IGNORECASE,
        )
    )
    all_caps = any(char.isalpha() for char in label) and label == label.upper()
    title_like = len(label.split()) <= 8 and not stripped.endswith(".")
    return label if (taxonomy_hit and title_like) or numbered or all_caps else None


def segment_contract(document: ContractDocument) -> tuple[ClauseSegment, ...]:
    text = document.text
    document_sha256 = _sha256(text)
    line_starts = [0]
    line_starts.extend(match.end() for match in re.finditer(r"\n", text))
    boundaries: list[tuple[int, str | None]] = []
    for start in line_starts:
        end = text.find("\n", start)
        if end == -1:
            end = len(text)
        heading = _looks_like_heading(text[start:end].rstrip("\r"))
        if heading is not None:
            boundaries.append((start, heading))
    if not boundaries or boundaries[0][0] > 0:
        boundaries.insert(0, (0, None))
    segments: list[ClauseSegment] = []
    for index, (start, heading) in enumerate(boundaries[:MAX_SEGMENTS]):
        raw_end = (
            boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        )
        end = raw_end
        while end > start and text[end - 1].isspace():
            end -= 1
        while start < end and text[start].isspace():
            start += 1
        if start >= end:
            continue
        body = text[start:end]
        excerpt = _excerpt(body)
        clause_type, evidence = _classify(heading, body)
        segments.append(
            ClauseSegment(
                clause_id=_derived_id(
                    "clause", document.document_id, len(segments) + 1
                ),
                document_id=document.document_id,
                source_id=document.source_id,
                document_sha256=document_sha256,
                clause_type=clause_type,
                heading=heading,
                char_start=start,
                char_end=end,
                line_start=_line_number(text, start),
                line_end=_line_number(text, end - 1),
                excerpt=excerpt,
                span_sha256=_sha256(body),
                excerpt_sha256=_sha256(excerpt),
                evidence=evidence,
            )
        )
    return tuple(segments[:MAX_SEGMENTS])


def _detected_segment_count(text: str) -> int:
    starts = [0]
    starts.extend(match.end() for match in re.finditer(r"\n", text))
    boundaries = 0
    first_boundary: int | None = None
    for start in starts:
        end = text.find("\n", start)
        if end == -1:
            end = len(text)
        if _looks_like_heading(text[start:end].rstrip("\r")) is not None:
            boundaries += 1
            if first_boundary is None:
                first_boundary = start
    if boundaries == 0 or (first_boundary is not None and first_boundary > 0):
        boundaries += 1
    return boundaries


def _finding(
    rule_id: str,
    occurrence: int,
    severity: Severity,
    title: str,
    explanation: str,
    method: Literal["literal_phrase", "absence_check"],
    matched_terms: tuple[str, ...] = (),
    clause: ClauseSegment | None = None,
    finding_type: Literal["risk", "playbook", "policy"] = "risk",
) -> LegalFinding:
    category = clause.clause_type if clause else None
    category_key = category.value if category else "document"
    match_key = hashlib.sha256(
        "|".join(sorted(value.casefold() for value in matched_terms)).encode("utf-8")
    ).hexdigest()[:12]
    return LegalFinding(
        risk_id=_derived_id(
            "risk", rule_id, category_key, match_key, occurrence
        ),
        rule_id=rule_id,
        finding_type=finding_type,
        severity=severity,
        title=title,
        explanation=explanation,
        clause_type=category,
        clause_id=clause.clause_id if clause else None,
        document_id=clause.document_id if clause else None,
        document_sha256=clause.document_sha256 if clause else None,
        char_start=clause.char_start if clause else None,
        char_end=clause.char_end if clause else None,
        line_start=clause.line_start if clause else None,
        line_end=clause.line_end if clause else None,
        excerpt=clause.excerpt if clause else None,
        span_sha256=clause.span_sha256 if clause else None,
        excerpt_sha256=clause.excerpt_sha256 if clause else None,
        evidence=MethodEvidence(
            method=method,
            confidence=0.94 if method == "literal_phrase" else 1.0,
            matched_terms=matched_terms,
            explanation=(
                "A deterministic literal phrase rule matched."
                if method == "literal_phrase"
                else "A deterministic required-category absence check matched."
            ),
        ),
    )


def _literal_matches(text: str, phrases: tuple[str, ...]) -> tuple[str, ...]:
    folded = text.casefold()
    return tuple(phrase for phrase in phrases if phrase.casefold() in folded)


def _collect_legal_risks(
    document: ContractDocument,
    clauses: tuple[ClauseSegment, ...],
    playbook: ContractPlaybook | None = None,
) -> tuple[tuple[LegalFinding, ...], int]:
    findings: list[LegalFinding] = []
    occurrence: dict[str, int] = {}
    omitted = 0

    def add(
        rule: str,
        severity: Severity,
        title: str,
        explanation: str,
        method: Literal["literal_phrase", "absence_check"],
        matches: tuple[str, ...] = (),
        clause: ClauseSegment | None = None,
        finding_type: Literal["risk", "playbook", "policy"] = "risk",
    ) -> None:
        nonlocal omitted
        occurrence[rule] = occurrence.get(rule, 0) + 1
        if len(findings) >= MAX_FINDINGS:
            omitted += 1
            return
        findings.append(
            _finding(
                rule, occurrence[rule], severity, title, explanation, method,
                matches[:12], clause, finding_type
            )
        )

    present = {clause.clause_type for clause in clauses}
    for clause in clauses:
        folded = document.text[clause.char_start:clause.char_end].casefold()
        if clause.clause_type in {ClauseType.LIABILITY, ClauseType.INDEMNITY}:
            matches = _literal_matches(
                folded, ("unlimited liability", "liability shall be unlimited", "no limitation of liability")
            )
            if matches:
                add(
                    "unlimited-liability", Severity.CRITICAL, "Potential unlimited liability",
                    "The clause uses language indicating liability may be uncapped.",
                    "literal_phrase", matches, clause,
                )
        if clause.clause_type == ClauseType.INDEMNITY:
            matches = _literal_matches(
                folded,
                ("any and all claims", "all claims, losses", "arising out of or relating to", "defend, indemnify and hold harmless"),
            )
            if matches:
                add(
                    "broad-indemnity", Severity.HIGH, "Potentially broad indemnity",
                    "The indemnity includes broad scope language; allocation and exclusions require review.",
                    "literal_phrase", matches, clause,
                )
        if clause.clause_type == ClauseType.TERMINATION:
            one_party = _literal_matches(
                folded,
                ("customer may terminate", "client may terminate", "company may terminate", "vendor may terminate"),
            )
            mutual = _literal_matches(folded, ("either party may terminate", "each party may terminate"))
            if one_party and not mutual:
                add(
                    "unilateral-termination", Severity.HIGH, "Potential unilateral termination right",
                    "A named party has an express termination right without detected reciprocal wording.",
                    "literal_phrase", one_party, clause,
                )
        if clause.clause_type in {ClauseType.RENEWAL, ClauseType.TERM}:
            matches = _literal_matches(
                folded, ("automatically renew", "automatic renewal", "auto-renew")
            )
            if matches:
                add(
                    "automatic-renewal", Severity.MEDIUM, "Automatic renewal",
                    "The agreement appears to renew automatically; notice timing should be reviewed.",
                    "literal_phrase", matches, clause,
                )
        if clause.clause_type == ClauseType.ASSIGNMENT:
            matches = _literal_matches(
                folded, ("may assign without consent", "without the prior written consent", "shall not assign")
            )
            if "may assign without consent" in matches or (
                "without the prior written consent" in matches
                and "shall not assign" in matches
            ):
                add(
                    "assignment-imbalance", Severity.MEDIUM, "Potential assignment imbalance",
                    "The assignment language may permit one party to assign while restricting the other.",
                    "literal_phrase", matches, clause,
                )

    for clause_type, rule, title in (
        (ClauseType.CONFIDENTIALITY, "confidentiality-gap", "No confidentiality clause detected"),
        (ClauseType.DATA_PROTECTION, "data-protection-gap", "No data-protection clause detected"),
    ):
        if clause_type not in present:
            add(
                rule, Severity.HIGH, title,
                f"No clause classified as {clause_type.value} was detected; applicability depends on the transaction.",
                "absence_check",
            )

    if playbook:
        for clause_type in playbook.required_clause_types:
            if clause_type not in present:
                add(
                    f"missing-{clause_type.value}", Severity.HIGH,
                    f"Required {clause_type.value.replace('_', ' ')} clause missing",
                    "The selected playbook requires this clause category, but none was detected.",
                    "absence_check", finding_type="playbook",
                )
        if playbook.liability_cap_required:
            liability_text = " ".join(
                document.text[c.char_start:c.char_end].casefold()
                for c in clauses if c.clause_type == ClauseType.LIABILITY
            )
            cap_terms = ("liability cap", "shall not exceed", "aggregate liability", "limited to")
            if not any(term in liability_text for term in cap_terms):
                add(
                    "missing-liability-cap", Severity.CRITICAL, "Required liability cap not detected",
                    "The playbook requires a liability cap, but no supported cap phrase was detected.",
                    "absence_check", finding_type="playbook",
                )
        for phrase in playbook.prohibited_literal_phrases:
            if phrase.casefold() in document.text.casefold():
                clause = next(
                    (
                        item for item in clauses
                        if phrase.casefold()
                        in document.text[item.char_start:item.char_end].casefold()
                    ),
                    None,
                )
                add(
                    "prohibited-phrase", Severity.HIGH, "Prohibited literal phrase detected",
                    "A caller-supplied playbook phrase was matched literally (never as a regular expression).",
                    "literal_phrase", (phrase,), clause, "playbook",
                )
        if playbook.preferred_governing_law:
            law_clauses = [
                clause for clause in clauses
                if clause.clause_type == ClauseType.GOVERNING_LAW
            ]
            if law_clauses and all(
                playbook.preferred_governing_law.casefold()
                not in document.text[c.char_start:c.char_end].casefold()
                for c in law_clauses
            ):
                add(
                    "governing-law-mismatch", Severity.HIGH, "Governing-law mismatch",
                    "A governing-law clause was detected but did not contain the playbook's preferred law.",
                    "literal_phrase", (playbook.preferred_governing_law,), law_clauses[0],
                    "playbook",
                )
        for policy in playbook.review_policies:
            for clause in clauses:
                if policy.clause_types and clause.clause_type not in policy.clause_types:
                    continue
                matches = _literal_matches(
                    document.text[clause.char_start:clause.char_end],
                    policy.literal_phrases,
                )
                if matches or (not policy.literal_phrases and policy.clause_types):
                    add(
                        f"policy-{policy.policy_id}", policy.severity, policy.title,
                        policy.explanation,
                        "literal_phrase" if matches else "absence_check",
                        matches, clause, "policy",
                    )
    return tuple(findings), omitted


def find_legal_risks(
    document: ContractDocument,
    clauses: tuple[ClauseSegment, ...],
    playbook: ContractPlaybook | None = None,
) -> tuple[LegalFinding, ...]:
    findings, _omitted = _collect_legal_risks(document, clauses, playbook)
    return findings


def analyze_contract(data: ContractAnalysisInput) -> ContractAnalysis:
    detected_clause_count = _detected_segment_count(data.document.text)
    clauses = segment_contract(data.document)
    findings, omitted_finding_count = _collect_legal_risks(
        data.document, clauses, data.playbook
    )
    warnings = [
        "This deterministic issue-spotting output is not legal advice.",
        "Document text is treated only as inert data; embedded instructions are never executed.",
    ]
    omitted_clause_count = max(0, detected_clause_count - len(clauses))
    if omitted_clause_count:
        warnings.append("Clause output reached its cap and may be truncated.")
    if omitted_finding_count:
        warnings.append("Finding output reached its cap and may be truncated.")
    return ContractAnalysis(
        source_citations=data.metadata.sources,
        document_id=data.document.document_id,
        source_id=data.document.source_id,
        document_sha256=_sha256(data.document.text),
        complete=not omitted_clause_count and not omitted_finding_count,
        omitted_clause_count=omitted_clause_count,
        omitted_finding_count=omitted_finding_count,
        clauses=clauses,
        findings=findings,
        assumptions=(
            "Character spans are zero-based, end-exclusive; line spans are one-based and inclusive.",
            "Classification uses fixed heading and body keywords, in declared taxonomy order.",
            "Playbook phrases are case-insensitive literal strings and are never regular expressions.",
        ),
        limitations=(
            "Rules cannot determine legal enforceability, commercial intent, negotiation context, or jurisdiction-specific effect.",
            "Declared jurisdiction is informational metadata and does not select or validate jurisdiction-specific law.",
            "Formatting without recognizable headings and synonyms outside the fixed taxonomy can reduce classification accuracy.",
            "Absence findings indicate that supported language was not detected, not that an obligation is legally absent.",
            "Analysis is transient and in-memory; this module performs no persistence, network, model, or tool calls.",
        ),
        warnings=tuple(warnings),
    )


def _comparison_tokens(value: str) -> Counter[str]:
    tokens: list[str] = []
    for match in re.finditer(r"[a-z0-9]{1,40}", value.casefold()):
        tokens.append(match.group(0))
        if len(tokens) >= 512:
            break
    return Counter(tokens)


def _bounded_similarity(original: str, revised: str) -> float:
    old_tokens = _comparison_tokens(original)
    new_tokens = _comparison_tokens(revised)
    total = sum(old_tokens.values()) + sum(new_tokens.values())
    if total == 0:
        return 1.0 if original == revised else 0.0
    overlap = sum(
        min(count, new_tokens.get(token, 0))
        for token, count in old_tokens.items()
    )
    return 2 * overlap / total


def _pair_clauses(
    original: tuple[ClauseSegment, ...],
    revised: tuple[ClauseSegment, ...],
    original_text: str,
    revised_text: str,
) -> tuple[ClauseChange, ...]:
    changes: list[ClauseChange] = []
    categories = list(dict.fromkeys(
        [clause.clause_type for clause in original]
        + [clause.clause_type for clause in revised]
    ))
    counter = 0
    for category in categories:
        old_group = [c for c in original if c.clause_type == category]
        new_group = [c for c in revised if c.clause_type == category]
        matched_old: set[int] = set()
        matched_new: set[int] = set()
        for old_index, old in enumerate(old_group):
            available = [
                index for index in range(len(new_group))
                if index not in matched_new
            ]
            if not available:
                continue
            projected = round(
                old_index * (len(new_group) - 1) / max(1, len(old_group) - 1)
            )
            candidate_indexes = sorted(
                available,
                key=lambda index: (
                    0
                    if new_group[index].heading == old.heading
                    and old.heading is not None
                    else 1,
                    abs(index - projected),
                    index,
                ),
            )[:MAX_CANDIDATES_PER_CLAUSE]
            old_value = original_text[old.char_start:old.char_end]
            scored: list[tuple[float, int]] = []
            for new_index in candidate_indexes:
                new = new_group[new_index]
                if old.span_sha256 == new.span_sha256:
                    score = 1.0
                else:
                    score = _bounded_similarity(
                        old_value,
                        revised_text[new.char_start:new.char_end],
                    )
                scored.append((score, new_index))
            scored.sort(key=lambda item: (-item[0], item[1]))
            if not scored or scored[0][0] < 0.35:
                continue
            score, new_index = scored[0]
            ambiguous_count = sum(
                1
                for candidate_score, _index in scored[1:]
                if candidate_score >= 0.35
                and abs(score - candidate_score) <= 0.03
            )
            matched_old.add(old_index)
            matched_new.add(new_index)
            new = new_group[new_index]
            exact = old.span_sha256 == new.span_sha256
            status: Literal["modified", "unchanged", "ambiguous"]
            if exact:
                status = "unchanged"
                match_basis = "exact_hash"
            elif ambiguous_count:
                status = "ambiguous"
                match_basis = "ambiguous_category_similarity"
            else:
                status = "modified"
                match_basis = "category_token_similarity"
            counter += 1
            changes.append(
                ClauseChange(
                    change_id=_derived_id("change", counter),
                    clause_type=category,
                    status=status,
                    match_basis=match_basis,
                    ambiguous_candidate_count=ambiguous_count,
                    original_clause_id=old.clause_id,
                    revised_clause_id=new.clause_id,
                    original_source_id=old.source_id,
                    revised_source_id=new.source_id,
                    original_document_sha256=old.document_sha256,
                    revised_document_sha256=new.document_sha256,
                    original_char_start=old.char_start,
                    original_char_end=old.char_end,
                    revised_char_start=new.char_start,
                    revised_char_end=new.char_end,
                    original_span_sha256=old.span_sha256,
                    revised_span_sha256=new.span_sha256,
                    similarity=round(score, 6),
                    original_excerpt=old.excerpt,
                    revised_excerpt=new.excerpt,
                    evidence=MethodEvidence(
                        method="comparison",
                        confidence=1.0,
                        matched_terms=(category.value,),
                        explanation=(
                            "Clauses aligned by exact span hash."
                            if exact
                            else "Clauses aligned by category and bounded token similarity; ambiguity is reported separately."
                        ),
                    ),
                )
            )
        for old_index, old in enumerate(old_group):
            if old_index not in matched_old:
                counter += 1
                changes.append(
                    ClauseChange(
                        change_id=_derived_id("change", counter),
                        clause_type=category,
                        status="removed",
                        match_basis="unmatched_category",
                        ambiguous_candidate_count=0,
                        original_clause_id=old.clause_id,
                        original_source_id=old.source_id,
                        original_document_sha256=old.document_sha256,
                        original_char_start=old.char_start,
                        original_char_end=old.char_end,
                        original_span_sha256=old.span_sha256,
                        similarity=0.0,
                        original_excerpt=old.excerpt,
                        evidence=MethodEvidence(
                            method="comparison", confidence=1.0,
                            matched_terms=(category.value,),
                            explanation="No revised clause in the same category met the similarity threshold.",
                        ),
                    )
                )
        for new_index, new in enumerate(new_group):
            if new_index not in matched_new:
                counter += 1
                changes.append(
                    ClauseChange(
                        change_id=_derived_id("change", counter),
                        clause_type=category,
                        status="added",
                        match_basis="unmatched_category",
                        ambiguous_candidate_count=0,
                        revised_clause_id=new.clause_id,
                        revised_source_id=new.source_id,
                        revised_document_sha256=new.document_sha256,
                        revised_char_start=new.char_start,
                        revised_char_end=new.char_end,
                        revised_span_sha256=new.span_sha256,
                        similarity=0.0,
                        revised_excerpt=new.excerpt,
                        evidence=MethodEvidence(
                            method="comparison", confidence=1.0,
                            matched_terms=(category.value,),
                            explanation="No original clause in the same category met the similarity threshold.",
                        ),
                    )
                )
    return tuple(changes)


def _risk_delta_key(finding: LegalFinding) -> tuple[str, str, str]:
    category = finding.clause_type.value if finding.clause_type else "document"
    parameter = ""
    if finding.rule_id == "prohibited-phrase":
        parameter = hashlib.sha256(
            "|".join(
                sorted(term.casefold() for term in finding.evidence.matched_terms)
            ).encode("utf-8")
        ).hexdigest()[:12]
    return finding.rule_id, category, parameter


def _risk_delta_id(key: tuple[str, str, str], occurrence: int) -> str:
    rule_id, category, parameter = key
    return _derived_id("risk", rule_id, category, parameter, occurrence)


def compare_contracts(data: ContractComparisonInput) -> ContractComparison:
    original_detected_count = _detected_segment_count(data.original.text)
    revised_detected_count = _detected_segment_count(data.revised.text)
    original_clauses = segment_contract(data.original)
    revised_clauses = segment_contract(data.revised)
    original_risks, original_omitted_findings = _collect_legal_risks(
        data.original, original_clauses, data.playbook
    )
    revised_risks, revised_omitted_findings = _collect_legal_risks(
        data.revised, revised_clauses, data.playbook
    )
    original_omitted_clauses = max(
        0, original_detected_count - len(original_clauses)
    )
    revised_omitted_clauses = max(
        0, revised_detected_count - len(revised_clauses)
    )
    old_risk_counts = Counter(
        _risk_delta_key(finding) for finding in original_risks
    )
    new_risk_counts = Counter(
        _risk_delta_key(finding) for finding in revised_risks
    )
    risk_keys = sorted(set(old_risk_counts) | set(new_risk_counts))
    persistent_ids: list[str] = []
    resolved_ids: list[str] = []
    new_ids: list[str] = []
    for key in risk_keys:
        persistent_count = min(old_risk_counts[key], new_risk_counts[key])
        persistent_ids.extend(
            _risk_delta_id(key, occurrence)
            for occurrence in range(1, persistent_count + 1)
        )
        resolved_ids.extend(
            _risk_delta_id(key, occurrence)
            for occurrence in range(
                persistent_count + 1, old_risk_counts[key] + 1
            )
        )
        new_ids.extend(
            _risk_delta_id(key, occurrence)
            for occurrence in range(
                persistent_count + 1, new_risk_counts[key] + 1
            )
        )
    all_changes = _pair_clauses(
        original_clauses, revised_clauses, data.original.text, data.revised.text
    )
    changes = all_changes[:MAX_COMPARISONS]
    warnings = [
        "This deterministic comparison and issue-spotting output is not legal advice.",
        "Document text is treated only as inert data; embedded instructions are never executed.",
    ]
    omitted_change_count = max(0, len(all_changes) - len(changes))
    if omitted_change_count:
        warnings.append("Comparison output reached its cap and may be truncated.")
    if original_omitted_clauses or revised_omitted_clauses:
        warnings.append(
            "One or both clause sets reached the extraction cap; comparison is incomplete."
        )
    if original_omitted_findings or revised_omitted_findings:
        warnings.append(
            "One or both risk sets reached the finding cap; risk deltas are incomplete."
        )
    return ContractComparison(
        source_citations=data.metadata.sources,
        original_document_id=data.original.document_id,
        revised_document_id=data.revised.document_id,
        original_source_id=data.original.source_id,
        revised_source_id=data.revised.source_id,
        original_document_sha256=_sha256(data.original.text),
        revised_document_sha256=_sha256(data.revised.text),
        complete=not any(
            (
                omitted_change_count,
                original_omitted_clauses,
                revised_omitted_clauses,
                original_omitted_findings,
                revised_omitted_findings,
            )
        ),
        omitted_change_count=omitted_change_count,
        original_omitted_clause_count=original_omitted_clauses,
        revised_omitted_clause_count=revised_omitted_clauses,
        original_omitted_finding_count=original_omitted_findings,
        revised_omitted_finding_count=revised_omitted_findings,
        changes=changes,
        risk_delta=RiskDelta(
            new_risk_ids=tuple(new_ids),
            resolved_risk_ids=tuple(resolved_ids),
            persistent_risk_ids=tuple(persistent_ids),
        ),
        assumptions=(
            "Clauses are aligned by typed category, exact span hashes, and bounded token similarity.",
            "Token similarity uses at most 512 normalized tokens and 16 candidates per clause with a fixed 0.35 threshold.",
            "Risk-delta IDs use canonical rule and category identity; prohibited literals additionally include a phrase digest.",
        ),
        limitations=(
            "Moved or heavily rewritten clauses may appear as removed and added rather than modified.",
            "Text similarity is lexical and does not establish semantic or legal equivalence.",
            "Declared jurisdiction is informational metadata and does not select or validate jurisdiction-specific law.",
            "Risk deltas reflect only the fixed supported rules and supplied playbook.",
            "Comparison is transient and in-memory; this module performs no persistence, network, model, or tool calls.",
        ),
        warnings=tuple(warnings),
    )
