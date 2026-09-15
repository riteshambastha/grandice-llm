import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Header, HTTPException, status

from . import db
from .config import get_settings, resolve_model

KEY_PREFIX = "gll-"


@dataclass
class ApiKey:
    id: int
    name: str
    rpm_limit: int
    allowed_models: list[str] | None
    org_id: int | None = None
    org_name: str | None = None
    org_rpm_limit: int | None = None
    member_id: int | None = None
    member_name: str | None = None
    member_rpm_limit: int | None = None

    def may_use(self, model: str) -> bool:
        if self.allowed_models is None:
            return True
        normalized = model.removesuffix(":latest")
        return any(
            resolve_model(allowed).removesuffix(":latest") == normalized
            for allowed in self.allowed_models
        )


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw: str) -> str:
    # API keys carry 256 bits of entropy, so a plain digest is sufficient here;
    # there is nothing for an attacker to brute force offline.
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def create_key(
    name: str,
    rpm_limit: int | None = None,
    allowed_models: list[str] | None = None,
    org_id: int | None = None,
    member_id: int | None = None,
) -> tuple[str, int]:
    raw = generate_key()
    key_id = await db.execute(
        """INSERT INTO api_keys
               (key_hash, key_prefix, name, created_at, rpm_limit, allowed_models,
                org_id, member_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            hash_key(raw),
            raw[:12],
            name,
            _now(),
            rpm_limit,
            json.dumps(allowed_models) if allowed_models else None,
            org_id,
            member_id,
        ),
    )
    return raw, key_id


def _extract_bearer(authorization: str | None, x_api_key: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    if x_api_key:
        return x_api_key.strip()
    return None


async def require_api_key(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> ApiKey:
    raw = _extract_bearer(authorization, x_api_key)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Send 'Authorization: Bearer <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    row = await db.fetch_one(
        """SELECT k.id, k.name, k.revoked, k.rpm_limit, k.allowed_models,
                  k.org_id, k.member_id,
                  o.name AS org_name, o.rpm_limit AS org_rpm_limit,
                  o.status AS org_status,
                  m.name AS member_name, m.rpm_limit AS member_rpm_limit,
                  m.status AS member_status
           FROM api_keys k
           LEFT JOIN organizations o ON o.id = k.org_id
           LEFT JOIN members m ON m.id = k.member_id
           WHERE k.key_hash = ?""",
        (hash_key(raw),),
    )
    if row is None or row["revoked"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked API key."
        )

    if row["org_id"] is not None and row["org_status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This organization is not active.",
        )
    if row["member_id"] is not None and row["member_status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This member is not active.",
        )

    await db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), row["id"]))

    return ApiKey(
        id=row["id"],
        name=row["name"],
        rpm_limit=row["rpm_limit"] or get_settings().default_rpm_limit,
        allowed_models=json.loads(row["allowed_models"]) if row["allowed_models"] else None,
        org_id=row["org_id"],
        org_name=row["org_name"],
        org_rpm_limit=row["org_rpm_limit"],
        member_id=row["member_id"],
        member_name=row["member_name"],
        member_rpm_limit=row["member_rpm_limit"],
    )


async def require_admin(
    authorization: str | None = Header(default=None),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    expected = get_settings().admin_token
    if not expected:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured.")
    supplied = _extract_bearer(authorization, x_admin_token)
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Admin token required.")
