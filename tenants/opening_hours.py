"""Shop opening hours stored on shops.availability (JSONB).

Canonical object (Africa/Kampala, 24-hour HH:MM):

    {
      "timezone": "Africa/Kampala",
      "open_24_hours": false,
      "by_appointment": false,
      "note": "optional, <= 160 chars",
      "legacy_text": "original free text, when we had one",
      "days": {
        "mon": {"closed": false, "ranges": [{"open": "08:00", "close": "18:00"}]},
        "tue": {"closed": true, "ranges": []},
        ...
      }
    }

`days` is null when the schedule is unknown. Free text is kept on `legacy_text`.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

KAMPALA = ZoneInfo("Africa/Kampala")
WEEKDAYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_LABEL = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}
_NAME_TO_KEY = {
    "monday": "mon",
    "mon": "mon",
    "tuesday": "tue",
    "tue": "tue",
    "tues": "tue",
    "wednesday": "wed",
    "wed": "wed",
    "thursday": "thu",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "friday": "fri",
    "fri": "fri",
    "saturday": "sat",
    "sat": "sat",
    "sunday": "sun",
    "sun": "sun",
}
_CLOCK = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|–|—|-)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
    re.IGNORECASE,
)
_DAY_SPAN = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)"
    r"\s*(?:to|–|—|-|through)\s*"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b",
    re.IGNORECASE,
)
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    open: str
    close: str

    @field_validator("open", "close")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        text = str(value).strip()
        if not _HHMM.match(text):
            raise ValueError("time must be HH:MM in 24-hour form")
        return text


class DayHours(BaseModel):
    model_config = ConfigDict(extra="ignore")

    closed: bool = False
    ranges: list[TimeRange] = Field(default_factory=list)


class OpeningHours(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timezone: str = "Africa/Kampala"
    open_24_hours: bool = False
    by_appointment: bool = False
    note: str | None = None
    legacy_text: str | None = None
    days: dict[str, DayHours] | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, _value: str) -> str:
        return "Africa/Kampala"

    @field_validator("note", "legacy_text")
    @classmethod
    def _short(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) > 500:
            text = text[:500]
        return text

    @field_validator("note")
    @classmethod
    def _note_cap(cls, value: str | None) -> str | None:
        if value and len(value) > 160:
            raise ValueError("note must be 160 characters or fewer")
        return value


def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def _check_ranges(ranges: list[TimeRange]) -> None:
    spans: list[tuple[int, int]] = []
    for item in ranges:
        start = _minutes(item.open)
        end = _minutes(item.close)
        if start == end:
            raise ValueError("a time range cannot start and end at the same time")
        if end < start:
            end += 24 * 60
        spans.append((start, end))
    spans.sort()
    for prev, nxt in zip(spans, spans[1:]):
        if nxt[0] < prev[1]:
            raise ValueError("time ranges on the same day overlap")


def _blank(legacy_text: str | None = None) -> dict[str, Any]:
    return {
        "timezone": "Africa/Kampala",
        "open_24_hours": False,
        "by_appointment": False,
        "note": None,
        "legacy_text": legacy_text,
        "days": None,
    }


def _complete_days(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    out: dict[str, Any] = {}
    for key in WEEKDAYS:
        item = raw.get(key) or {}
        if isinstance(item, DayHours):
            ranges = [r.model_dump() for r in item.ranges]
            closed = item.closed or not ranges
        else:
            ranges = []
            for row in item.get("ranges") or []:
                if isinstance(row, TimeRange):
                    ranges.append(row.model_dump())
                elif isinstance(row, dict) and row.get("open") and row.get("close"):
                    ranges.append(TimeRange.model_validate(row).model_dump())
            closed = bool(item.get("closed")) or not ranges
        if not closed:
            _check_ranges([TimeRange.model_validate(r) for r in ranges])
        out[key] = {"closed": closed or not ranges, "ranges": [] if closed else ranges}
    return out


def _to_hour(value: int, ampm: str | None, *, assume_pm: bool) -> int:
    hour = value % 24
    if ampm:
        marker = ampm.lower()
        hour = hour % 12
        if marker == "pm":
            hour += 12
        return hour
    if assume_pm and hour < 12:
        return hour + 12
    return hour


def _parse_clock_range(text: str) -> tuple[str, str] | None:
    match = _CLOCK.search(text)
    if not match:
        return None
    h1, m1, ap1, h2, m2, ap2 = match.groups()
    open_h = int(h1)
    close_h = int(h2)
    if open_h > 23 or close_h > 23:
        return None
    open_m = int(m1 or 0)
    close_m = int(m2 or 0)
    if open_m > 59 or close_m > 59:
        return None
    assume_close_pm = False
    if not ap1 and not ap2 and ":" not in match.group(0):
        if close_h <= open_h or close_h <= 7:
            assume_close_pm = True
    elif not ap2 and ap1 and ap1.lower() == "am" and close_h <= open_h:
        assume_close_pm = True
    open_h = _to_hour(open_h, ap1, assume_pm=False)
    close_h = _to_hour(close_h, ap2, assume_pm=assume_close_pm and not ap2)
    if open_h > 23 or close_h > 23:
        return None
    return f"{open_h:02d}:{open_m:02d}", f"{close_h:02d}:{close_m:02d}"


def _part_of_day(text: str) -> tuple[str, str] | None:
    low = text.lower()
    if "morning" in low:
        return "08:00", "12:00"
    if "afternoon" in low:
        return "12:00", "17:00"
    if "evening" in low:
        return "17:00", "21:00"
    return None


def _days_in_clause(text: str) -> list[str]:
    low = text.lower()
    if any(token in low for token in ("every day", "everyday", "daily", "seven days", "7 days", "all week")):
        return list(WEEKDAYS)
    found: list[str] = []
    if "weekday" in low:
        found.extend(["mon", "tue", "wed", "thu", "fri"])
    if "weekend" in low:
        found.extend(["sat", "sun"])
    span = _DAY_SPAN.search(low)
    if span:
        start = _NAME_TO_KEY[span.group(1).lower()]
        end = _NAME_TO_KEY[span.group(2).lower()]
        i0 = WEEKDAYS.index(start)
        i1 = WEEKDAYS.index(end)
        if i1 < i0:
            keys = list(WEEKDAYS[i0:]) + list(WEEKDAYS[: i1 + 1])
        else:
            keys = list(WEEKDAYS[i0 : i1 + 1])
        found.extend(keys)
    else:
        for name, key in sorted(_NAME_TO_KEY.items(), key=lambda item: -len(item[0])):
            if re.search(rf"\b{name}\b", low) and key not in found:
                found.append(key)
    deduped: list[str] = []
    for key in found:
        if key not in deduped:
            deduped.append(key)
    return deduped


def parse_free_text(text: str) -> dict[str, Any]:
    """Best-effort parse. Unparsed text stays on legacy_text and days stays null."""
    raw = text.strip()
    if not raw:
        return _blank(None)
    low = raw.lower()
    hours = _blank(raw)
    if re.search(r"\b(24\s*/\s*7|24\s*hours|24hrs|open 24)\b", low):
        hours["open_24_hours"] = True
    if "appointment" in low:
        hours["by_appointment"] = True
    days: dict[str, list[tuple[str, str]]] = {key: [] for key in WEEKDAYS}
    parsed = False
    for clause in re.split(r"[,;]", raw):
        if not clause.strip():
            continue
        day_keys = _days_in_clause(clause)
        clock = _parse_clock_range(clause) or _part_of_day(clause)
        if not clock:
            continue
        if not day_keys and any(
            token in clause.lower() for token in ("every day", "everyday", "daily", "seven days", "7 days")
        ):
            day_keys = list(WEEKDAYS)
        if not day_keys:
            continue
        for key in day_keys:
            days[key].append(clock)
        parsed = True
    if not parsed:
        return hours
    hours["days"] = {
        key: {
            "closed": not slots,
            "ranges": [{"open": start, "close": end} for start, end in slots],
        }
        for key, slots in days.items()
    }
    return OpeningHours.model_validate(hours).model_dump()


def _legacy_text_from_dict(value: dict[str, Any]) -> str | None:
    for key in ("legacy_text", "text", "hours", "summary", "label", "raw"):
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    note = value.get("note")
    if isinstance(note, str) and note.strip() and "days" not in value:
        return note.strip()
    try:
        return json.dumps(value, default=str, ensure_ascii=False)[:500]
    except TypeError:
        return None


def normalize_for_storage(value: Any) -> dict[str, Any] | None:
    """Validate writer input. Free text is parsed; structured days are strict."""
    if value is None:
        return None
    if isinstance(value, str):
        return parse_free_text(value)
    if isinstance(value, OpeningHours):
        dumped = value.model_dump()
        dumped["days"] = _complete_days(value.days)
        return dumped
    if not isinstance(value, dict):
        raise ValueError("availability must be an object or text")
    if not value:
        return None
    structured = any(key in value for key in ("days", "open_24_hours", "by_appointment", "legacy_text"))
    if structured and value.get("days") is not None:
        model = OpeningHours.model_validate(value)
        dumped = model.model_dump()
        dumped["days"] = _complete_days(model.days)
        if dumped["note"] and len(dumped["note"]) > 160:
            raise ValueError("note must be 160 characters or fewer")
        return dumped
    if structured:
        model = OpeningHours.model_validate(value)
        dumped = model.model_dump()
        dumped["days"] = None
        return dumped
    text = _legacy_text_from_dict(value)
    parsed = parse_free_text(text) if text else _blank(None)
    if parsed.get("days") is None and text:
        parsed["legacy_text"] = text[:500]
    return parsed


def coerce_stored(value: Any) -> dict[str, Any] | None:
    """Read path. Never raises on old rows."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            return parse_free_text(value)
        if isinstance(value, dict):
            return normalize_for_storage(value)
    except Exception:
        text = value if isinstance(value, str) else _legacy_text_from_dict(value if isinstance(value, dict) else {})
        return _blank(text)
    return _blank(None)


def _local_now(now: datetime | None) -> datetime:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(KAMPALA)


def _intervals(hours: dict[str, Any], now_local: datetime) -> list[tuple[datetime, datetime]]:
    days = hours.get("days") or {}
    if not isinstance(days, dict):
        return []
    start_date = now_local.date() - timedelta(days=1)
    found: list[tuple[datetime, datetime]] = []
    for offset in range(0, 9):
        day = start_date + timedelta(days=offset)
        key = WEEKDAYS[day.weekday()]
        spec = days.get(key) or {}
        if spec.get("closed"):
            continue
        for row in spec.get("ranges") or []:
            try:
                open_h, open_m = (int(x) for x in str(row["open"]).split(":"))
                close_h, close_m = (int(x) for x in str(row["close"]).split(":"))
                begin = datetime.combine(day, time(open_h, open_m), tzinfo=KAMPALA)
                end = datetime.combine(day, time(close_h, close_m), tzinfo=KAMPALA)
            except (KeyError, TypeError, ValueError):
                continue
            if end <= begin:
                end += timedelta(days=1)
            found.append((begin, end))
    found.sort()
    return found


def opening_status(
    hours: dict[str, Any] | None,
    now: datetime | None = None,
) -> tuple[bool | None, str | None]:
    """Return (is_open_now, next_change). Unknown schedules use null."""
    if not hours:
        return None, None
    if hours.get("open_24_hours"):
        return True, "Open 24 hours"
    if not hours.get("days"):
        if hours.get("by_appointment"):
            return None, "By appointment"
        return None, None
    now_local = _local_now(now)
    intervals = _intervals(hours, now_local)
    for begin, end in intervals:
        if begin <= now_local < end:
            end_local = end.astimezone(KAMPALA)
            stamp = end_local.strftime("%H:%M")
            if end_local.date() == now_local.date():
                return True, f"Closes {stamp}"
            label = WEEKDAY_LABEL[WEEKDAYS[end_local.weekday()]]
            return True, f"Closes {label} {stamp}"
    for begin, _end in intervals:
        if begin > now_local:
            label = WEEKDAY_LABEL[WEEKDAYS[begin.weekday()]]
            return False, f"Opens {label} {begin.strftime('%H:%M')}"
    if hours.get("by_appointment"):
        return False, "By appointment"
    return False, None


def public_hours(value: Any, now: datetime | None = None) -> dict[str, Any]:
    """Fields added to shop responses."""
    hours = coerce_stored(value)
    is_open, nxt = opening_status(hours, now)
    return {
        "availability": hours,
        "availability_text": (hours or {}).get("legacy_text") if hours else None,
        "is_open_now": is_open,
        "next_change": nxt,
    }
