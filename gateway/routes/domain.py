from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..auth import ApiKey, require_api_key
from .openai import _enforce_limit

router = APIRouter(prefix="/v1/domain", tags=["domain-audit"])


@router.get("/runs/{request_id}")
async def get_domain_run(
    request_id: str,
    key: ApiKey = Depends(require_api_key),
) -> dict[str, Any]:
    _enforce_limit(key)
    row = await db.fetch_one(
        """SELECT request_id, ts, endpoint, agent, privacy_mode, status,
                  duration_ms, input_schema, output_schema, source_count,
                  warning_count, methodology_version
           FROM domain_runs
           WHERE request_id = ? AND key_id = ?""",
        (request_id, key.id),
    )
    if row is None:
        raise HTTPException(404, "Domain run not found.")
    return {
        "object": "domain.run",
        **dict(row),
        "content_retained": False,
    }

