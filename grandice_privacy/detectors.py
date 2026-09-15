from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .models import Finding, PrivacyPolicy


@dataclass(frozen=True)
class PatternDetector:
    entity_type: str
    pattern: re.Pattern[str]
    confidence: float
    validator: Callable[[str], bool] | None = None


def _luhn(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _valid_iban(value: str) -> bool:
    compact = re.sub(r"\s+", "", value).upper()
    if not 15 <= len(compact) <= 34 or not compact[:2].isalpha():
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(ord(char) - 55) if char.isalpha() else char for char in rearranged)
    return int(numeric) % 97 == 1


DETECTORS = (
    PatternDetector(
        "EMAIL_ADDRESS",
        re.compile(r"(?<![\w.+-])[\w.+-]+@(?:[\w-]+\.)+[A-Za-z]{2,63}(?![\w-])"),
        0.98,
    ),
    PatternDetector(
        "US_SSN",
        re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}[- ](?!00)\d{2}[- ](?!0000)\d{4}(?!\d)"),
        0.98,
    ),
    PatternDetector(
        "CREDIT_CARD",
        re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)"),
        0.95,
        _luhn,
    ),
    PatternDetector(
        "IBAN",
        re.compile(r"(?<![A-Z0-9])[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}(?![A-Z0-9])", re.I),
        0.96,
        _valid_iban,
    ),
    PatternDetector(
        "IP_ADDRESS",
        re.compile(
            r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
            r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
        ),
        0.90,
    ),
    PatternDetector(
        "PHONE_NUMBER",
        re.compile(r"(?<!\w)(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]\d{4}(?!\w)"),
        0.82,
    ),
    PatternDetector(
        "DATE_OF_BIRTH",
        re.compile(
            r"(?i)\b(?:dob|date\s+of\s+birth|born)\s*(?::|is)?\s*"
            r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
            r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|"
            r"dec(?:ember)?)\s+\d{1,2},?\s+\d{4})"
        ),
        0.92,
    ),
    PatternDetector(
        "ROUTING_NUMBER",
        re.compile(r"(?i)\b(?:routing|aba)\s*(?:number|no\.?|#)?\s*[:=-]?\s*(\d{9})\b"),
        0.94,
    ),
    PatternDetector(
        "ACCOUNT_NUMBER",
        re.compile(r"(?i)\b(?:account|acct)\s*(?:number|no\.?|#)?\s*[:=-]?\s*([A-Z0-9-]{6,24})\b"),
        0.90,
    ),
    PatternDetector(
        "CASE_NUMBER",
        re.compile(r"(?i)\b(?:case|matter|docket)\s*(?:number|no\.?|#)?\s*[:=-]?\s*([A-Z0-9-]{4,30})\b"),
        0.88,
    ),
    PatternDetector(
        "FINANCIAL_VALUE",
        re.compile(r"(?<!\w)(?:USD|CAD|GBP|EUR|\$|£|€)\s?\d[\d,]*(?:\.\d{1,2})?(?!\w)", re.I),
        0.86,
    ),
    PatternDetector(
        "API_KEY",
        re.compile(
            r"(?i)\b(?:api[_ -]?key|secret|token|password)\s*[:=]\s*"
            r"['\"]?([A-Za-z0-9_./+=-]{12,})['\"]?"
        ),
        0.93,
    ),
)


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def detect_text(text: str, policy: PrivacyPolicy, path: str = "$") -> list[Finding]:
    candidates: list[Finding] = []
    for detector in DETECTORS:
        if detector.entity_type not in policy.entities:
            continue
        for match in detector.pattern.finditer(text):
            value = match.group(0)
            if detector.validator and not detector.validator(value):
                continue
            if detector.confidence < policy.min_confidence:
                continue
            candidates.append(
                Finding(
                    entity_type=detector.entity_type,
                    path=path,
                    start=match.start(),
                    end=match.end(),
                    confidence=detector.confidence,
                    detector=f"pattern:{detector.entity_type.lower()}",
                )
            )

    for entity_type, literals in policy.custom_literals.items():
        for literal in literals:
            for match in re.finditer(re.escape(literal), text, re.IGNORECASE):
                candidates.append(
                    Finding(
                        entity_type=entity_type,
                        path=path,
                        start=match.start(),
                        end=match.end(),
                        confidence=1.0,
                        detector="custom-literal",
                    )
                )

    # Prefer higher-confidence and longer findings when detectors overlap.
    accepted: list[Finding] = []
    for finding in sorted(
        candidates,
        key=lambda item: (-item.confidence, -(item.end - item.start), item.start),
    ):
        span = (finding.start, finding.end)
        if not any(_overlaps(span, (item.start, item.end)) for item in accepted):
            accepted.append(finding)
    return sorted(accepted, key=lambda item: item.start)

