"""Conservative extraction of a household request from a speech transcript.

Speech recognition is fallible, so this module produces a draft for review. It
never starts a provider call or guesses missing booking constraints.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schemas import VoiceDraft, VoiceInterpretRead

try:
    INDIA_TZ = ZoneInfo("Asia/Kolkata")
except ZoneInfoNotFoundError:
    # The Windows development runtime may not have an IANA timezone database.
    # India has a fixed UTC+05:30 offset and does not observe daylight saving.
    INDIA_TZ = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
MONTHS = {
    name: number
    for number, name in enumerate(
        ("january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"),
        start=1,
    )
}
MONTH_NAMES = "|".join(MONTHS)
DATE_PATTERNS = (
    re.compile(r"(?<!\w)(\d{4})-(\d{1,2})-(\d{1,2})(?!\w)", re.I),
    re.compile(
        rf"(?<!\w)(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?"
        rf"({MONTH_NAMES})\s*,?\s*(\d{{4}})?(?!\w)",
        re.I,
    ),
    re.compile(
        rf"(?<!\w)({MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?"
        r"\s*,?\s*(\d{4})?(?!\w)",
        re.I,
    ),
    re.compile(r"(?<!\w)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\w)", re.I),
)
HOUR_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
HOUR_TOKEN = r"(?:\d{1,2}|" + "|".join(HOUR_WORDS) + r")"
MERIDIEM_TOKEN = r"(?:[ap]\s*\.?\s*m\.?|in\s+the\s+(?:morning|afternoon|evening))"
SPOKEN_TIME_PATTERN = re.compile(
    rf"(?<!\w)(?:(?P<direction>after|before|from|at|around)\s+)?"
    rf"(?P<hour>{HOUR_TOKEN})(?::(?P<minute>\d{{1,2}}))?\s*"
    rf"(?:o['’]?clock\s*)?(?P<meridiem>{MERIDIEM_TOKEN})(?!\w)",
    re.I,
)
TIME_PATTERN = re.compile(
    r"\b(after|before|from|at|around)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
    re.I,
)
DAYPART_WINDOWS = {
    "morning": (6 * 60, 12 * 60, 10 * 60),
    "afternoon": (12 * 60, 17 * 60, 14 * 60),
    "evening": (17 * 60, 21 * 60, 18 * 60),
}
BUDGET_PATTERN = re.compile(
    r"\b(?:under|below|less than|maximum|max|budget(?:\s+(?:of|is))?|up to|within)"
    r"\s*(?:₹|rs\.?|inr)?\s*([\d,]+)(?:\s*rupees?)?\b",
    re.I,
)
ADDRESS_PATTERN = re.compile(r"\b(?:address\s+is|at)\s+(.+?)(?:[.!?]|$)", re.I)


def _clock_minutes(hour: str, minute: str | None, meridiem: str) -> int | None:
    h = HOUR_WORDS.get(hour.lower(), None)
    if h is None:
        h = int(hour)
    m = int(minute or 0)
    if not 1 <= h <= 12 or m > 59:
        return None
    period = meridiem.lower().replace(".", "").replace(" ", "")
    if period == "intheafternoon" or period == "intheevening":
        period = "pm"
    elif period == "inthemorning":
        period = "am"
    return (h % 12 + (12 if period == "pm" else 0)) * 60 + m


def _format_clock(total_minutes: int) -> str:
    hour, minute = divmod(total_minutes, 60)
    return f"{hour % 12 or 12}:{minute:02d} {'PM' if hour >= 12 else 'AM'}"


def extract_time_window(transcript: str) -> str | None:
    # Later turns may correct an earlier time, so the last explicit time wins.
    for match in reversed(list(SPOKEN_TIME_PATTERN.finditer(transcript))):
        minutes = _clock_minutes(match["hour"], match["minute"], match["meridiem"])
        if minutes is not None:
            direction = (match["direction"] or "at").lower()
            label = {"before": "Before", "after": "After", "from": "After",
                     "at": "At", "around": "Around"}[direction]
            return f"{label} {_format_clock(minutes)}"
    # A broad daypart is useful and reviewable; no exact appointment time is implied.
    matches = list(re.finditer(r"\b(morning|afternoon|evening)\b", transcript, re.I))
    return matches[-1][1].title() if matches else None


def _explicit_date(transcript: str, today) -> str | None:
    candidates = []
    for pattern_index, pattern in enumerate(DATE_PATTERNS):
        candidates.extend(
            (match.start(), pattern_index, match) for match in pattern.finditer(transcript)
        )
    if not candidates:
        return None
    _, pattern_index, match = max(candidates, key=lambda item: item[0])
    if pattern_index == 0:
        year, month, day = map(int, match.groups())
    elif pattern_index == 1:
        day, month_name, year_text = match.groups()
        month, day = MONTHS[month_name.lower()], int(day)
        year = int(year_text) if year_text else None
    elif pattern_index == 2:
        month_name, day, year_text = match.groups()
        month, day = MONTHS[month_name.lower()], int(day)
        year = int(year_text) if year_text else None
    else:
        day, month, year = map(int, match.groups())  # Indian DD/MM/YYYY ordering.
    if year is None:
        year = today.year
    try:
        candidate = today.replace(year=year, month=month, day=day)
        if candidate < today and not match.group(3):
            candidate = candidate.replace(year=year + 1)
    except ValueError:
        return None
    return candidate.isoformat() if candidate >= today else None


def _extract_date(transcript: str, now: datetime) -> str | None:
    text = transcript.lower()
    today = now.astimezone(INDIA_TZ).date()
    if any(pattern.search(text) for pattern in DATE_PATTERNS):
        return _explicit_date(text, today)
    if re.search(r"\bday\s+after\s+tomorrow\b", text):
        return (today + timedelta(days=2)).isoformat()
    if re.search(r"\btomorrow\b", text):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\btoday\b", text):
        return today.isoformat()
    match = re.search(
        r"\b(?:(next|this|coming)\s+)?"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        text,
    )
    if not match:
        return None
    delta = (WEEKDAYS[match[2]] - today.weekday()) % 7
    if match[1] == "next":
        delta += 7
    return (today + timedelta(days=delta)).isoformat()


def _extract_address(transcript: str) -> str | None:
    match = ADDRESS_PATTERN.search(transcript)
    if not match:
        return None
    candidate = match[1].strip(" ,")
    if re.match(r"^(?:my\s+)?(?:home|place)\b", candidate, re.I):
        return None
    # The generic "at" preposition also introduces appointment times. An ASR
    # transcript may spell the hour out or punctuate p.m. into separate pieces.
    if re.match(
        rf"^{HOUR_TOKEN}(?::\d{{1,2}})?\s*(?:[ap](?:\.?m)?|in\s+the\s+(?:morning|afternoon|evening)|o['’]?clock)(?:\b|\.|$)",
        candidate,
        re.I,
    ):
        return None
    return candidate[:500] if len(candidate) >= 5 else None


def interpret_request(
    transcript: str,
    *,
    saved_address: str | None = None,
    now: datetime | None = None,
) -> VoiceInterpretRead:
    """Extract only supported AC-servicing constraints and explain omissions."""
    now = now or datetime.now(INDIA_TZ)
    service_type = (
        "AC servicing"
        if re.search(r"\b(?:ac|a\.?c\.?|air\s+condition(?:er|ing))\b", transcript, re.I)
        else None
    )
    requested_date = _extract_date(transcript, now)
    time_window = extract_time_window(transcript)
    budget_match = BUDGET_PATTERN.search(transcript)
    budget = int(budget_match[1].replace(",", "")) if budget_match else None
    if budget is not None and not 1 <= budget <= 1_000_000:
        budget = None
    explicit_address = _extract_address(transcript)
    address = explicit_address or saved_address

    draft = VoiceDraft(
        description=transcript.strip(),
        service_type=service_type,
        requested_date=requested_date,
        time_window=time_window,
        budget_rupees=budget,
        address=address,
    )
    missing_fields = [
        field
        for field in ("service_type", "requested_date", "time_window", "budget_rupees", "address")
        if getattr(draft, field) is None
    ]
    if missing_fields:
        labels = {
            "service_type": "which service you need",
            "requested_date": "the date",
            "time_window": "a time such as after 2 PM",
            "budget_rupees": "your maximum budget",
            "address": "the service address",
        }
        reply = "I heard your request. Please tell me " + ", ".join(
            labels[field] for field in missing_fields
        ) + "."
    else:
        memory_note = " using your saved address" if not explicit_address and saved_address else ""
        reply = (
            f"I heard {service_type} on {requested_date}, {time_window.lower()}, "
            f"with a maximum budget of ₹{budget:,} at {address}{memory_note}. "
            "Please review these details before I start."
        )
    return VoiceInterpretRead(
        transcript=transcript.strip(),
        draft=draft,
        missing_fields=missing_fields,
        ready=not missing_fields,
        reply=reply,
    )


def slot_matches_request(*, requested_date: str, time_window: str, slot: str) -> bool:
    """Recognize the supported day/time forms; unknown forms need review."""
    requested = requested_date.strip().lower()
    offered = slot.strip().lower()
    if not requested or not offered:
        return False
    iso_match = re.fullmatch(r"\d{4}-\d{2}-\d{2}", requested)
    if iso_match:
        if requested not in offered:
            return False
    elif requested in WEEKDAYS:
        if not re.search(rf"\b{re.escape(requested)}\b", offered):
            return False
    else:
        return False

    offered_times = list(re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", slot, re.I))
    if not offered_times:
        return False
    start_match = offered_times[0]
    start = _clock_minutes(start_match[1], start_match[2], start_match[3])
    if start is None:
        return False
    end = None
    if len(offered_times) > 1:
        end_match = offered_times[1]
        end = _clock_minutes(end_match[1], end_match[2], end_match[3])
    daypart = DAYPART_WINDOWS.get(time_window.strip().lower())
    if daypart:
        earliest, latest, _ = daypart
        return earliest <= start and end is not None and end <= latest
    constraint = TIME_PATTERN.search(time_window)
    if not constraint:
        return False
    limit = _clock_minutes(constraint[2], constraint[3], constraint[4])
    if limit is None:
        return False
    direction = constraint[1].lower()
    if direction == "before":
        if len(offered_times) < 2:
            return False
        return end is not None and end <= limit
    if direction == "at":
        return start == limit
    if direction == "around":
        return abs(start - limit) <= 30
    return start >= limit


def simulated_slot(requested_date: str, time_window: str) -> str | None:
    """Offer a demo slot inside an understood window, or ask for human review."""
    daypart = DAYPART_WINDOWS.get(time_window.strip().lower())
    if daypart:
        start = daypart[2]
        return f"{requested_date}, {_format_clock(start)} - {_format_clock(start + 60)}"
    constraint = TIME_PATTERN.search(time_window)
    if not constraint:
        return None
    limit = _clock_minutes(constraint[2], constraint[3], constraint[4])
    if limit is None:
        return None
    direction = constraint[1].lower()
    if direction == "before":
        end = min(16 * 60, limit)
        start = end - 60
    elif direction in {"at", "around"}:
        start = limit
        end = start + 60
    else:
        start = max(16 * 60, limit + 60)
        end = start + 60
    if start < 0 or end >= 24 * 60:
        return None
    return f"{requested_date}, {_format_clock(start)} - {_format_clock(end)}"
