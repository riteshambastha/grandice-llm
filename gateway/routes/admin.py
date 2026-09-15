import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from .. import auth, backup, db, monitoring, upstream
from ..auth import require_admin
from ..config import get_settings

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

Status = Literal["active", "suspended"]
Role = Literal["admin", "member"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64] or "organization"


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    slug: str | None = Field(default=None, min_length=2, max_length=64)
    license_tier: str = Field(default="free", min_length=2, max_length=32)
    member_limit: int = Field(default=5, ge=1, le=10_000)
    rpm_limit: int = Field(default=120, ge=1, le=1_000_000)

    @field_validator("name", "slug", "license_tier")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    license_tier: str | None = Field(default=None, min_length=2, max_length=32)
    member_limit: int | None = Field(default=None, ge=1, le=10_000)
    rpm_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    status: Status | None = None


class MemberCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=254)
    role: Role = "member"
    rpm_limit: int = Field(default=60, ge=1, le=1_000_000)

    @field_validator("name", "email")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value.lower()


class MemberUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    email: str | None = Field(default=None, min_length=5, max_length=254)
    role: Role | None = None
    rpm_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    status: Status | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value.lower() if value else value


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64, description="Application this key belongs to.")
    member_id: int | None = Field(default=None, ge=1)
    rpm_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    allowed_models: list[str] | None = Field(
        default=None, description="Restrict the key to these models. Omit to allow all."
    )


class UpdateKeyRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    rpm_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    allowed_models: list[str] | None = None


class CreateKeyResponse(BaseModel):
    id: int
    name: str
    api_key: str = Field(description="Shown once. Store it now; only a hash is kept.")


async def _organization(org_id: int):
    row = await db.fetch_one("SELECT * FROM organizations WHERE id = ?", (org_id,))
    if row is None:
        raise HTTPException(404, f"No organization with id {org_id}.")
    return row


async def _member(member_id: int):
    row = await db.fetch_one(
        """SELECT m.*, o.name AS org_name, o.status AS org_status
           FROM members m JOIN organizations o ON o.id = m.org_id
           WHERE m.id = ?""",
        (member_id,),
    )
    if row is None:
        raise HTTPException(404, f"No member with id {member_id}.")
    return row


@router.get("/overview")
async def overview() -> dict[str, Any]:
    counts = await db.fetch_one(
        """SELECT
             (SELECT COUNT(*) FROM organizations WHERE status = 'active') AS organizations,
             (SELECT COUNT(*) FROM members WHERE status = 'active') AS members,
             (SELECT COUNT(*) FROM api_keys WHERE revoked = 0) AS active_keys,
             (SELECT COUNT(*) FROM usage_log
                WHERE ts >= datetime('now', '-24 hours')) AS requests_24h,
             (SELECT COUNT(*) FROM usage_log
                WHERE ts >= datetime('now', '-24 hours') AND status >= 400) AS errors_24h"""
    )
    return dict(counts) if counts else {}


@router.post("/organizations", status_code=201)
async def create_organization(req: OrganizationCreate) -> dict[str, Any]:
    slug = _slugify(req.slug or req.name)
    timestamp = _now()
    try:
        org_id = await db.execute(
            """INSERT INTO organizations
               (name, slug, license_tier, member_limit, rpm_limit, status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                req.name,
                slug,
                req.license_tier,
                req.member_limit,
                req.rpm_limit,
                timestamp,
                timestamp,
            ),
        )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(409, f"Organization slug '{slug}' already exists.") from exc
        raise
    return {"id": org_id, **req.model_dump(), "slug": slug, "status": "active"}


@router.get("/organizations")
async def list_organizations() -> dict[str, Any]:
    rows = await db.fetch_all(
        """SELECT o.*,
                  (SELECT COUNT(*) FROM members m WHERE m.org_id = o.id) AS member_count,
                  (SELECT COUNT(*) FROM api_keys k
                     WHERE k.org_id = o.id AND k.revoked = 0) AS active_keys,
                  (SELECT COUNT(*) FROM usage_log u
                     WHERE u.org_id = o.id
                       AND u.ts >= datetime('now', '-7 days')) AS requests_7d
           FROM organizations o
           ORDER BY o.name"""
    )
    return {"organizations": [dict(row) for row in rows]}


@router.patch("/organizations/{org_id}")
async def update_organization(
    org_id: int, req: OrganizationUpdate
) -> dict[str, Any]:
    await _organization(org_id)
    updates = req.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        raise HTTPException(400, "No organization fields supplied.")
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{field} = ?" for field in updates)
    await db.execute(
        f"UPDATE organizations SET {assignments} WHERE id = ?",
        (*updates.values(), org_id),
    )
    return dict(await _organization(org_id))


@router.post("/organizations/{org_id}/members", status_code=201)
async def create_member(org_id: int, req: MemberCreate) -> dict[str, Any]:
    organization = await _organization(org_id)
    if organization["status"] != "active":
        raise HTTPException(409, "Cannot add members to a suspended organization.")
    count = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM members WHERE org_id = ?", (org_id,)
    )
    if count and count["count"] >= organization["member_limit"]:
        raise HTTPException(
            409,
            f"Member limit of {organization['member_limit']} reached for this organization.",
        )
    timestamp = _now()
    try:
        member_id = await db.execute(
            """INSERT INTO members
               (org_id, name, email, role, rpm_limit, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                org_id,
                req.name,
                req.email,
                req.role,
                req.rpm_limit,
                timestamp,
                timestamp,
            ),
        )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(409, "That email already belongs to this organization.") from exc
        raise
    return {"id": member_id, "org_id": org_id, **req.model_dump(), "status": "active"}


@router.get("/organizations/{org_id}/members")
async def list_members(org_id: int) -> dict[str, Any]:
    await _organization(org_id)
    rows = await db.fetch_all(
        """SELECT m.*,
                  (SELECT COUNT(*) FROM api_keys k
                     WHERE k.member_id = m.id AND k.revoked = 0) AS active_keys,
                  (SELECT COUNT(*) FROM usage_log u
                     WHERE u.member_id = m.id
                       AND u.ts >= datetime('now', '-7 days')) AS requests_7d
           FROM members m
           WHERE m.org_id = ?
           ORDER BY m.name""",
        (org_id,),
    )
    return {"members": [dict(row) for row in rows]}


@router.get("/members")
async def list_all_members() -> dict[str, Any]:
    rows = await db.fetch_all(
        """SELECT m.*, o.name AS org_name,
                  (SELECT COUNT(*) FROM api_keys k
                     WHERE k.member_id = m.id AND k.revoked = 0) AS active_keys,
                  (SELECT COUNT(*) FROM usage_log u
                     WHERE u.member_id = m.id
                       AND u.ts >= datetime('now', '-7 days')) AS requests_7d
           FROM members m JOIN organizations o ON o.id = m.org_id
           ORDER BY o.name, m.name"""
    )
    return {"members": [dict(row) for row in rows]}


@router.patch("/members/{member_id}")
async def update_member(member_id: int, req: MemberUpdate) -> dict[str, Any]:
    await _member(member_id)
    updates = req.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        raise HTTPException(400, "No member fields supplied.")
    updates["updated_at"] = _now()
    assignments = ", ".join(f"{field} = ?" for field in updates)
    try:
        await db.execute(
            f"UPDATE members SET {assignments} WHERE id = ?",
            (*updates.values(), member_id),
        )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise HTTPException(409, "That email already belongs to this organization.") from exc
        raise
    return dict(await _member(member_id))


@router.post("/keys", response_model=CreateKeyResponse, status_code=201)
async def create_key(req: CreateKeyRequest) -> CreateKeyResponse:
    org_id = None
    if req.member_id is not None:
        member = await _member(req.member_id)
        if member["status"] != "active" or member["org_status"] != "active":
            raise HTTPException(409, "Cannot create a key for an inactive account.")
        org_id = member["org_id"]
    raw, key_id = await auth.create_key(
        req.name,
        req.rpm_limit,
        req.allowed_models,
        org_id=org_id,
        member_id=req.member_id,
    )
    return CreateKeyResponse(id=key_id, name=req.name, api_key=raw)


@router.get("/keys")
async def list_keys() -> dict[str, Any]:
    rows = await db.fetch_all(
        """SELECT k.id, k.key_prefix, k.name, k.created_at, k.last_used_at,
                  k.revoked, k.rpm_limit, k.allowed_models, k.org_id, k.member_id,
                  o.name AS org_name, m.name AS member_name, m.email AS member_email
           FROM api_keys k
           LEFT JOIN organizations o ON o.id = k.org_id
           LEFT JOIN members m ON m.id = k.member_id
           ORDER BY k.id DESC"""
    )
    return {
        "keys": [
            {
                **dict(row),
                "key_prefix": row["key_prefix"] + "...",
                "revoked": bool(row["revoked"]),
                "allowed_models": (
                    json.loads(row["allowed_models"]) if row["allowed_models"] else None
                ),
            }
            for row in rows
        ]
    }


@router.patch("/keys/{key_id}")
async def update_key(key_id: int, req: UpdateKeyRequest) -> dict[str, Any]:
    row = await db.fetch_one("SELECT id FROM api_keys WHERE id = ?", (key_id,))
    if row is None:
        raise HTTPException(404, f"No API key with id {key_id}.")
    updates = req.model_dump(exclude_unset=True)
    if "allowed_models" in updates:
        updates["allowed_models"] = (
            json.dumps(updates["allowed_models"]) if updates["allowed_models"] else None
        )
    if not updates:
        raise HTTPException(400, "No key fields supplied.")
    assignments = ", ".join(f"{field} = ?" for field in updates)
    await db.execute(
        f"UPDATE api_keys SET {assignments} WHERE id = ?",
        (*updates.values(), key_id),
    )
    return {"id": key_id, "updated": True}


@router.post("/keys/{key_id}/rotate", response_model=CreateKeyResponse, status_code=201)
async def rotate_key(key_id: int) -> CreateKeyResponse:
    row = await db.fetch_one("SELECT * FROM api_keys WHERE id = ?", (key_id,))
    if row is None:
        raise HTTPException(404, f"No API key with id {key_id}.")
    if row["revoked"]:
        raise HTTPException(409, "Cannot rotate a revoked key.")
    raw, new_id = await auth.create_key(
        row["name"],
        row["rpm_limit"],
        json.loads(row["allowed_models"]) if row["allowed_models"] else None,
        org_id=row["org_id"],
        member_id=row["member_id"],
    )
    await db.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
    return CreateKeyResponse(id=new_id, name=row["name"], api_key=raw)


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: int) -> None:
    row = await db.fetch_one("SELECT id FROM api_keys WHERE id = ?", (key_id,))
    if row is None:
        raise HTTPException(404, f"No API key with id {key_id}.")
    await db.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))


@router.get("/usage")
async def usage_summary(
    days: int = Query(default=7, ge=1, le=365),
    org_id: int | None = Query(default=None, ge=1),
    member_id: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    filters = ["ts >= datetime('now', ?)"]
    params: list[Any] = [f"-{days} days"]
    if org_id is not None:
        filters.append("org_id = ?")
        params.append(org_id)
    if member_id is not None:
        filters.append("member_id = ?")
        params.append(member_id)
    where = " AND ".join(filters)

    summary = await db.fetch_one(
        f"""SELECT COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   CAST(COALESCE(AVG(duration_ms), 0) AS INTEGER) AS avg_duration_ms,
                   SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors
            FROM usage_log WHERE {where}""",
        params,
    )
    timeseries = await db.fetch_all(
        f"""SELECT date(ts) AS date, COUNT(*) AS requests,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors,
                   CAST(AVG(duration_ms) AS INTEGER) AS avg_duration_ms
            FROM usage_log WHERE {where}
            GROUP BY date(ts) ORDER BY date(ts)""",
        params,
    )

    async def breakdown(column: str, label: str) -> list[dict[str, Any]]:
        rows = await db.fetch_all(
            f"""SELECT COALESCE({column}, 'Unassigned') AS {label},
                       COUNT(*) AS requests,
                       COALESCE(SUM(total_tokens), 0) AS total_tokens,
                       CAST(AVG(duration_ms) AS INTEGER) AS avg_duration_ms,
                       SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors
                FROM usage_log WHERE {where}
                GROUP BY {column} ORDER BY requests DESC LIMIT 50""",
            params,
        )
        return [dict(row) for row in rows]

    return {
        "window_days": days,
        "summary": dict(summary) if summary else {},
        "timeseries": [dict(row) for row in timeseries],
        "by_organization": await breakdown("org_name", "organization"),
        "by_member": await breakdown("member_name", "member"),
        "by_application": await breakdown("app_name", "application"),
        "by_model": await breakdown("model", "model"),
    }


@router.get("/monitoring")
async def system_monitoring() -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    loaded_models: list[dict[str, Any]] = []
    ollama_reachable = False
    try:
        tags = await upstream.client().get("/api/tags", timeout=5.0)
        tags.raise_for_status()
        models = tags.json().get("models", [])
        ollama_reachable = True
        running = await upstream.client().get("/api/ps", timeout=5.0)
        if running.status_code == 200:
            loaded_models = running.json().get("models", [])
    except Exception:
        pass

    recent = await db.fetch_one(
        """SELECT COUNT(*) AS requests,
                  CAST(COALESCE(AVG(duration_ms), 0) AS INTEGER) AS avg_duration_ms,
                  SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors
           FROM usage_log WHERE ts >= datetime('now', '-24 hours')"""
    )
    return {
        "ollama_reachable": ollama_reachable,
        "installed_models": [
            {"name": model.get("name"), "size": model.get("size")}
            for model in models
        ],
        "loaded_models": [
            {
                "name": model.get("name"),
                "size_vram": model.get("size_vram"),
                "expires_at": model.get("expires_at"),
            }
            for model in loaded_models
        ],
        "gpu": await monitoring.gpu_snapshot(),
        "runtime": monitoring.runtime_snapshot(),
        "requests_24h": dict(recent) if recent else {},
        "backup": backup.status(),
    }


@router.get("/backups")
async def backup_history() -> dict[str, Any]:
    return {"status": backup.status(), "backups": backup.list_backups()}


@router.post("/backups", status_code=201)
async def run_backup() -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(backup.create_backup)
    except RuntimeError as exc:
        if "already running" in str(exc):
            raise HTTPException(409, str(exc)) from exc
        raise HTTPException(500, str(exc)) from exc
    return result


@router.post("/backups/{filename}/verify")
async def verify_stored_backup(filename: str) -> dict[str, Any]:
    if filename != Path(filename).name or not backup.BACKUP_PATTERN.match(filename):
        raise HTTPException(400, "Invalid backup filename.")
    path = get_settings().backup_dir / filename
    if not path.exists():
        raise HTTPException(404, "Backup not found.")
    try:
        return await asyncio.to_thread(backup.verify_backup, path)
    except Exception as exc:
        raise HTTPException(500, f"Backup verification failed: {exc}") from exc
