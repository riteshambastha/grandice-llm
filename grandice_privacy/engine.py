from __future__ import annotations

import secrets
from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any

from .detectors import detect_text
from .media import MediaPrivacyError, embedded_media_type
from .models import Finding, PrivacyReceipt, PrivacyResult, Transformation
from .policies import get_policy


class TokenVault:
    """An in-memory substitution map intended to remain on the caller's device."""

    def __init__(self) -> None:
        self._token_to_value: dict[str, str] = {}
        self._value_to_token: dict[tuple[str, str], str] = {}
        self._counts: defaultdict[str, int] = defaultdict(int)

    def token_for(self, entity_type: str, value: str) -> str:
        key = (entity_type, value)
        existing = self._value_to_token.get(key)
        if existing:
            return existing
        self._counts[entity_type] += 1
        token = f"<GI_{entity_type}_{self._counts[entity_type]:03d}>"
        self._value_to_token[key] = token
        self._token_to_value[token] = value
        return token

    def restore_text(self, text: str) -> str:
        result = text
        for token, value in sorted(
            self._token_to_value.items(), key=lambda item: len(item[0]), reverse=True
        ):
            result = result.replace(token, value)
        return result

    def restore(self, data: Any) -> Any:
        if isinstance(data, str):
            return self.restore_text(data)
        if isinstance(data, list):
            return [self.restore(item) for item in data]
        if isinstance(data, tuple):
            return tuple(self.restore(item) for item in data)
        if isinstance(data, dict):
            return {key: self.restore(value) for key, value in data.items()}
        return data

    def restore_response(self, data: Any) -> Any:
        """Restore only user-visible text, never model-controlled tool data."""
        safe_text_keys = {"content", "text", "output_text"}
        blocked_keys = {
            "tool_calls",
            "tool_call",
            "function_call",
            "function",
            "arguments",
            "input",
        }

        def walk(value: Any, parent_key: str | None = None) -> Any:
            if isinstance(value, str):
                return (
                    self.restore_text(value)
                    if parent_key in safe_text_keys
                    else value
                )
            if isinstance(value, list):
                return [walk(item, parent_key) for item in value]
            if isinstance(value, dict):
                return {
                    key: item
                    if str(key).lower() in blocked_keys
                    else walk(item, str(key).lower())
                    for key, item in value.items()
                }
            return value

        return walk(data)

    @property
    def size(self) -> int:
        return len(self._token_to_value)


def _mask(entity_type: str, value: str) -> str:
    if entity_type == "EMAIL_ADDRESS" and "@" in value:
        local, domain = value.split("@", 1)
        return f"{local[:1]}***@{domain}"
    digits = [index for index, char in enumerate(value) if char.isdigit()]
    if entity_type in {"CREDIT_CARD", "ACCOUNT_NUMBER"} and len(digits) >= 4:
        visible = set(digits[-4:])
        return "".join(char if index in visible else "*" if char.isalnum() else char for index, char in enumerate(value))
    return f"<REDACTED_{entity_type}>"


class PrivacyEngine:
    def __init__(
        self,
        policy_id: str = "general-v1",
        *,
        custom_literals: dict[str, list[str]] | None = None,
        processing_location: str = "client",
        vault: TokenVault | None = None,
    ) -> None:
        self.policy = get_policy(policy_id, custom_literals=custom_literals)
        if processing_location not in {"client", "sidecar", "hosted"}:
            raise ValueError("processing_location must be client, sidecar, or hosted")
        self.processing_location = processing_location
        self.vault = vault or TokenVault()

    def inspect(self, data: Any) -> PrivacyResult:
        return self._process(data, "detect")

    def mask(self, data: Any) -> PrivacyResult:
        return self._process(data, "mask")

    def tokenize(self, data: Any) -> PrivacyResult:
        return self._process(data, "tokenize")

    def restore(self, data: Any) -> Any:
        return self.vault.restore(data)

    def restore_response(self, data: Any) -> Any:
        return self.vault.restore_response(data)

    def _process(self, data: Any, transformation: Transformation) -> PrivacyResult:
        findings: list[Finding] = []
        transformed = self._walk(data, "$", transformation, findings)
        detected_counts = Counter(item.entity_type for item in findings)
        transformed_counts = (
            detected_counts if transformation != "detect" else Counter()
        )
        receipt = PrivacyReceipt(
            request_id=f"prv_{secrets.token_urlsafe(12)}",
            processing_location=self.processing_location,  # type: ignore[arg-type]
            policy=self.policy.id,
            transformation=transformation,
            entities_detected=dict(sorted(detected_counts.items())),
            entities_transformed=dict(sorted(transformed_counts.items())),
            raw_content_transferred=self.processing_location == "hosted",
        )
        return PrivacyResult(data=transformed, findings=findings, receipt=receipt)

    def _walk(
        self,
        data: Any,
        path: str,
        transformation: Transformation,
        findings: list[Finding],
    ) -> Any:
        if isinstance(data, str):
            return self._transform_text(data, path, transformation, findings)
        if isinstance(data, list):
            return [
                self._walk(item, f"{path}[{index}]", transformation, findings)
                for index, item in enumerate(data)
            ]
        if isinstance(data, tuple):
            return tuple(
                self._walk(item, f"{path}[{index}]", transformation, findings)
                for index, item in enumerate(data)
            )
        if isinstance(data, dict):
            result: dict[Any, Any] = {}
            for key, value in data.items():
                output_key = key
                if self.policy.process_keys and isinstance(key, str):
                    output_key = self._transform_text(
                        key, f"{path}.<key>", transformation, findings
                    )
                child_path = f"{path}.{key}" if path != "$" else f"$.{key}"
                result[output_key] = self._walk(
                    value, child_path, transformation, findings
                )
            return result
        return data

    def _transform_text(
        self,
        text: str,
        path: str,
        transformation: Transformation,
        all_findings: list[Finding],
    ) -> str:
        media_type = embedded_media_type(text, path)
        if media_type and transformation != "detect":
            raise MediaPrivacyError(
                f"Embedded {media_type} media at {path} was not transferred. "
                "Complete local multimodal redaction is not available in this build."
            )
        findings = detect_text(text, self.policy, path)
        if transformation == "detect" or not findings:
            all_findings.extend(findings)
            return text

        result = text
        output_findings: list[Finding] = []
        for finding in reversed(findings):
            original = text[finding.start : finding.end]
            replacement = (
                _mask(finding.entity_type, original)
                if transformation == "mask"
                else self.vault.token_for(finding.entity_type, original)
            )
            result = result[: finding.start] + replacement + result[finding.end :]
            output_findings.append(replace(finding, replacement=replacement))
        all_findings.extend(reversed(output_findings))
        return result

