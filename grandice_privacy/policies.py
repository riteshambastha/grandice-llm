from __future__ import annotations

from dataclasses import replace

from .models import PrivacyPolicy

GENERAL_ENTITIES = frozenset(
    {
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "IP_ADDRESS",
        "US_SSN",
        "CREDIT_CARD",
        "IBAN",
        "API_KEY",
    }
)

POLICIES: dict[str, PrivacyPolicy] = {
    "general-v1": PrivacyPolicy(
        id="general-v1",
        entities=GENERAL_ENTITIES,
        min_confidence=0.80,
        fail_on_unsupported_media=False,
    ),
    "strict-v1": PrivacyPolicy(
        id="strict-v1",
        entities=GENERAL_ENTITIES | {"ACCOUNT_NUMBER", "DATE_OF_BIRTH"},
        min_confidence=0.70,
    ),
    "financial-strict-v1": PrivacyPolicy(
        id="financial-strict-v1",
        entities=GENERAL_ENTITIES
        | {"ACCOUNT_NUMBER", "ROUTING_NUMBER", "DATE_OF_BIRTH", "FINANCIAL_VALUE"},
        min_confidence=0.70,
    ),
    "legal-strict-v1": PrivacyPolicy(
        id="legal-strict-v1",
        entities=GENERAL_ENTITIES | {"ACCOUNT_NUMBER", "DATE_OF_BIRTH", "CASE_NUMBER"},
        min_confidence=0.70,
    ),
}


def get_policy(
    policy_id: str,
    *,
    custom_literals: dict[str, list[str]] | None = None,
) -> PrivacyPolicy:
    try:
        policy = POLICIES[policy_id]
    except KeyError as exc:
        available = ", ".join(sorted(POLICIES))
        raise ValueError(f"Unknown privacy policy '{policy_id}'. Available: {available}") from exc

    if not custom_literals:
        return policy

    normalized = {
        entity.upper(): tuple(value for value in values if value)
        for entity, values in custom_literals.items()
        if values
    }
    return replace(policy, custom_literals=normalized)


def public_policy(policy: PrivacyPolicy) -> dict:
    return {
        "id": policy.id,
        "entities": sorted(policy.entities),
        "min_confidence": policy.min_confidence,
        "process_keys": policy.process_keys,
        "fail_on_unsupported_media": policy.fail_on_unsupported_media,
    }

