"""Thai-first date parsing and timezone helpers.

Rules:
- "Today" always mean the user's local date, never the UTC date.
- Functions take `today` or `now` as an argument instead of reading the clock,
  so tests (and retried jobs) get the same answer every time.
- Years >= 2400 are Buddhist Era (B.E.); 2-digit years are B.E. too ("69" = 2569).
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BE_OFFSET = 543
BE_THRESHOLD = 2400

THAI_MONTHS = {
    "มกราคม": 1,
    "ม.ค.": 1,
    "กุมภาพันธ์": 2,
    "ก.พ.": 2,
    "มีนาคม": 3,
    "มี.ค.": 3,
    "เมษายน": 4,
    "เม.ย.": 4,
    "พฤษภาคม": 5,
    "พ.ค.": 5,
    "มิถุนายน": 6,
    "มิ.ย.": 6,
    "กรกฎาคม": 7,
    "ก.ค.": 7,
    "สิงหาคม": 8,
    "ส.ค.": 8,
    "กันยายน": 9,
    "ก.ย.": 9,
    "ตุลาคม": 10,
    "ต.ค.": 10,
    "พฤศจิกายน": 11,
    "พ.ย.": 11,
    "ธันวาคม": 12,
    "ธ.ค.": 12,
}

RELATIVE_DAYS = {
    "วันนี้": 0,
    "พรุ่งนี้": 1,
    "มะรืน": 2,
    "มะรืนนี้": 2,
}

ISO_DATE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
SLASH_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")
THAI_DATE = re.compile(r"^(\d{1,2})\s*(\S+?)\s*(\d{2}|\d{4})?$")


class DateParseError(ValueError):
    """The text cannot parse to date or the date does not exist."""


def local_now(now: datetime, tz: str) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ZoneInfo(tz))


def local_today(now: datetime, tz: str) -> date:
    return local_now(now, tz).date()


def day_start_utc(day: date, tz: str) -> datetime:
    """Midnight at the start of 'day' in 'tz', converted to UTC."""
    return datetime.combine(day, time.min, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _normalize_year(year: int, digits: int) -> int:
    if digits == 2:
        year += 2500
    if year >= BE_THRESHOLD:
        year -= BE_OFFSET
    return year


def _make_date(year: int, month: int, day: int) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise DateParseError(f"date does not exist: {day}/{month}/{year}") from exc


def _next_occurrence(month: int, day: int, today: date) -> date:
    """No year given: use this year, or next year if that date already passed."""
    candidate = _make_date(today.year, month, day)
    if candidate < today:
        candidate = _make_date(today.year + 1, month, day)
    return candidate


def parse_date(text: str, today: date) -> date:
    cleaned = " ".join(text.split())

    if cleaned in RELATIVE_DAYS:
        return today + timedelta(days=RELATIVE_DAYS[cleaned])

    if match := ISO_DATE.match(cleaned):
        year, month, day = (int(g) for g in match.groups())
        return _make_date(_normalize_year(year, 4), month, day)

    if match := SLASH_DATE.match(cleaned):
        day, month, year_text = match.groups()
        year = _normalize_year(int(year_text), len(year_text))
        return _make_date(year, int(month), int(day))

    if match := THAI_DATE.match(cleaned):
        day, month_text, year_text = match.groups()
        month = THAI_MONTHS.get(month_text)
        if month is None:
            raise DateParseError(f"unknown month: {month_text!r}")
        if year_text is None:
            return _next_occurrence(month, int(day), today)
        year = _normalize_year(int(year_text), len(year_text))
        return _make_date(year, month, int(day))

    raise DateParseError(f"not a date: {text!r}")
