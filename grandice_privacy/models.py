from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Transformation = Literal["detect", "mask", "tokenize"]


@dataclass(frozen=True)
class Finding:
    entity_type: str
    path: str
    start: int
    end: int
    confidence: float
    detector: str
    replacement: str | None = None

    def public_dict(self) -> dict[str, Any]:
        """Return metadata only. The original sensitive value is never exposed."""
        result: dict[str, Any] = {
            "entity_type": self.entity_type,
            "path": self.path,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence,
            "detector": self.detector,
        }
        if self.replacement is not None:
            result["replacement"] = self.replacement
        return result


@dataclass(frozen=True)
class PrivacyPolicy:
    id: str
    entities: frozenset[str]
    min_confidence: float = 0.75
    process_keys: bool = False
    fail_on_unsupported_media: bool = True
    custom_literals: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class PrivacyReceipt:
    request_id: str
    processing_location: Literal["client", "sidecar", "hosted"]
    policy: str
    transformation: Transformation
    entities_detected: dict[str, int]
    entities_transformed: dict[str, int]
    raw_content_transferred: bool
    content_retained: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "processing_location": self.processing_location,
            "policy": self.policy,
            "transformation": self.transformation,
            "entities_detected": self.entities_detected,
            "entities_transformed": self.entities_transformed,
            "raw_content_transferred": self.raw_content_transferred,
            "content_retained": self.content_retained,
            "warnings": self.warnings,
        }


@dataclass
class PrivacyResult:
    data: Any
    findings: list[Finding]
    receipt: PrivacyReceipt

