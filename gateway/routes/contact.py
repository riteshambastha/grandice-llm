import logging
import re
import time
from collections import defaultdict, deque
from threading import Lock

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .. import db
from ..config import get_settings

log = logging.getLogger("gateway.contact")
router = APIRouter(tags=["contact"])

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
ALLOWED_HOSTNAMES = {"grand-ice.com", "www.grand-ice.com"}
CONTACT_LIMIT = 5
CONTACT_WINDOW_SECONDS = 600
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_hits: dict[str, deque[float]] = defaultdict(deque)
_hits_lock = Lock()


class ContactSubmission(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=254)
    question: str = Field(min_length=10, max_length=5000)
    turnstile_token: str = Field(min_length=1, max_length=2048)
    website: str = Field(default="", max_length=200)

    @field_validator("name", "email", "question")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address.")
        return value


def _client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client else "unknown"
    )


def _rate_limit(client_ip: str) -> int:
    now = time.monotonic()
    cutoff = now - CONTACT_WINDOW_SECONDS
    with _hits_lock:
        bucket = _hits[client_ip]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= CONTACT_LIMIT:
            return max(1, int(bucket[0] + CONTACT_WINDOW_SECONDS - now) + 1)
        bucket.append(now)
    return 0


async def _verify_turnstile(token: str) -> bool:
    settings = get_settings()
    if not settings.turnstile_secret_key:
        log.error("TURNSTILE_SECRET_KEY is not configured.")
        raise HTTPException(status_code=503, detail="Contact form is not configured.")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                TURNSTILE_VERIFY_URL,
                data={"secret": settings.turnstile_secret_key, "response": token},
            )
        response.raise_for_status()
        result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Turnstile verification failed: %s", exc)
        raise HTTPException(
            status_code=503, detail="Human verification is temporarily unavailable."
        ) from exc

    return bool(
        result.get("success")
        and result.get("hostname") in ALLOWED_HOSTNAMES
        and result.get("action") == "contact"
    )


@router.post("/contact", status_code=201)
async def submit_contact(
    submission: ContactSubmission, request: Request
) -> dict[str, str]:
    # Honeypot fields are invisible to people but commonly filled by simple bots.
    if submission.website:
        return {
            "message": "Thank you for reaching out, we will get back to you via email."
        }

    retry_after = _rate_limit(_client_ip(request))
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail=f"Too many submissions. Please retry in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )

    if not await _verify_turnstile(submission.turnstile_token):
        raise HTTPException(
            status_code=400, detail="Human verification failed. Please try again."
        )

    submission_id = await db.execute(
        """
        INSERT INTO contact_submissions
            (created_at, name, email, question, email_status)
        VALUES (datetime('now'), ?, ?, ?, 'pending')
        """,
        (submission.name, submission.email, submission.question),
    )

    settings = get_settings()
    email_status = "sent"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                settings.contact_form_endpoint,
                headers={
                    "Accept": "application/json",
                    "Origin": "https://grand-ice.com",
                    "Referer": "https://grand-ice.com/contact",
                },
                json={
                    "Name": submission.name,
                    "Email": submission.email,
                    "Question / Feedback": submission.question,
                    "_subject": "New Grandice website enquiry",
                    "_template": "table",
                    "_captcha": "false",
                },
            )
        response.raise_for_status()
        relay_result = response.json()
        if str(relay_result.get("success", "")).lower() == "false":
            relay_message = relay_result.get("message", "Email relay rejected submission.")
            if "needs activation" in relay_message.lower():
                email_status = "activation_required"
            else:
                raise ValueError(relay_message)
    except (httpx.HTTPError, ValueError) as exc:
        await db.execute(
            "UPDATE contact_submissions SET email_status = 'failed' WHERE id = ?",
            (submission_id,),
        )
        log.warning("Contact email relay failed for submission %s: %s", submission_id, exc)
        raise HTTPException(
            status_code=502,
            detail="We could not deliver your message right now. Please try again.",
        ) from exc

    await db.execute(
        "UPDATE contact_submissions SET email_status = ? WHERE id = ?",
        (email_status, submission_id),
    )
    log.info(
        "Contact submission %s accepted with email status %s.",
        submission_id,
        email_status,
    )
    return {
        "message": "Thank you for reaching out, we will get back to you via email."
    }
