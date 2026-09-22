"""Isolation and data minimization for an opt-in, anonymous public demo."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, Request

from app.config import Settings

COOKIE_NAME = "jeevan_demo_session"
COOKIE_AGE_SECONDS = 7 * 24 * 60 * 60
DEMO_ADDRESS = "Demo apartment, HSR Layout, Bengaluru"
_DAY = re.compile(r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)$", re.I)
_TIME = re.compile(
    r"^(?:(?:After|Before|At|Around) (?:1[0-2]|[1-9]):[0-5]\d [AP]M|Morning|Afternoon|Evening)$",
    re.I,
)


def validate_demo_settings(settings: Settings) -> None:
    if not settings.public_demo_mode:
        return
    if not settings.demo_session_secret or len(settings.demo_session_secret) < 32:
        raise RuntimeError(
            "PUBLIC_DEMO_MODE requires DEMO_SESSION_SECRET of at least 32 characters"
        )
    if settings.voice_adapter != "simulated" or settings.booking_adapter != "simulated":
        raise RuntimeError("Public demo mode requires simulated voice and booking adapters")
    if settings.demo_max_tasks_per_session < 1 or settings.demo_max_tasks_total < 1:
        raise RuntimeError("Public demo task caps must be positive")


def _signature(session_id: str, issued_at: str, secret: str) -> str:
    message = f"{session_id}.{issued_at}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def new_session_token(settings: Settings) -> tuple[str, str]:
    session_id = str(uuid.uuid4())
    issued_at = str(int(time.time()))
    signature = _signature(session_id, issued_at, settings.demo_session_secret or "")
    return session_id, f"{session_id}.{issued_at}.{signature}"


def session_id_from_token(token: str | None, settings: Settings) -> str | None:
    if not token or len(token) > 200:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    session_id, issued_at, signature = parts
    try:
        if str(uuid.UUID(session_id)) != session_id:
            return None
        issued = int(issued_at)
    except ValueError:
        return None
    now = int(time.time())
    if issued > now + 60 or now - issued > COOKIE_AGE_SECONDS:
        return None
    expected = _signature(session_id, issued_at, settings.demo_session_secret or "")
    return session_id if hmac.compare_digest(signature, expected) else None


def session_user_id(request: Request) -> str:
    user_id = getattr(request.state, "demo_user_id", None)
    if not user_id:
        raise HTTPException(status_code=403, detail="Demo session required")
    return user_id


def task_owner(request: Request, user_id: str | None = None) -> str | None:
    from app.config import get_settings

    return session_user_id(request) if get_settings().public_demo_mode else user_id


def memory_owner(request: Request, supplied_user_id: str) -> str:
    from app.config import get_settings

    if not get_settings().public_demo_mode:
        return supplied_user_id
    # The existing frontend calls the demo-user alias. It never selects the owner.
    if supplied_user_id != "demo-user":
        raise HTTPException(status_code=404, detail="Memory not found")
    return session_user_id(request)


def checked_demo_date(value: str) -> str:
    value = value.strip()
    if _DAY.fullmatch(value):
        return value.title()
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Use a weekday or YYYY-MM-DD demo date"
        ) from exc
    india_today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    if parsed.isoformat() != value or parsed < india_today:
        raise HTTPException(status_code=422, detail="Use a present or future demo date")
    return value


def checked_demo_time(value: str) -> str:
    value = value.strip()
    if not _TIME.fullmatch(value):
        raise HTTPException(status_code=422, detail="Use a supported demo time window")
    return value


def demo_idempotency_key(user_id: str, supplied_key: str) -> str:
    return f"{user_id}:{hashlib.sha256(supplied_key.encode()).hexdigest()[:32]}"
