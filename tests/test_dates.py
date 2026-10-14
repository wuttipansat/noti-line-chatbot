from datetime import UTC, date, datetime
import pytest

from app.services.dates import DateParseError, day_start_utc, local_today, parse_date

BKK = "Asia/Bangkok"
TODAY = date(2026, 10, 14)


@pytest.mark.parametrize(
    "text",
    [
        "15/11/2569",
        "15 พ.ย. 69",
        "2026-11-15",
        "15/11/69",
        "15/11/2026",
        "15 พฤศจิกายน 2569",
        "15พ.ย.2569",
        "  15   พ.ย.   69  ",
    ],
)
def test_b_e_parsing(text):
    assert parse_date(text, TODAY) == date(2026, 11, 15)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("วันนี้", date(2026, 10, 14)),
        ("พรุ่งนี้", date(2026, 10, 15)),
        ("มะรืนนี้", date(2026, 10, 16)),
    ],
)
def test_relative_days(text, expected):
    assert parse_date(text, TODAY) == expected


def test_relative_day_crosses_month_and_year():
    assert parse_date("พรุ่งนี้", date(2026, 12, 31)) == date(2027, 1, 1)


def test_month_without_year_picks_next_occurrence():
    assert parse_date("15 พ.ย.", TODAY) == date(2026, 11, 15)
    assert parse_date("1 ม.ค.", TODAY) == date(2027, 1, 1)
    assert parse_date("14 ต.ค.", TODAY) == TODAY


@pytest.mark.parametrize(
    "text",
    ["31/02/2569", "29 ก.พ. 69", "15 พย 69", "hello", "", "2026/11/15", "15/13/69"],
)
def test_rejects_bad_or_impossible_dates(text):
    with pytest.raises(DateParseError):
        parse_date(text, TODAY)


def test_leap_day_exists_in_2571():
    assert parse_date("29 ก.พ. 2571", TODAY) == date(2028, 2, 29)


@pytest.mark.parametrize(
    ("utc_now", "expected_local_day"),
    [
        (datetime(2026, 10, 14, 16, 30, tzinfo=UTC), date(2026, 10, 14)),  # 23:30 BKK
        (datetime(2026, 10, 14, 17, 30, tzinfo=UTC), date(2026, 10, 15)),  # 00:30 BKK
    ],
)
def test_local_today_uses_user_timezone_not_utc(utc_now, expected_local_day):
    assert utc_now.date() == date(2026, 10, 14)
    assert local_today(utc_now, BKK) == expected_local_day


def test_local_today_rejects_naive_datetime():
    with pytest.raises(ValueError, match="timezone-aware"):
        local_today(datetime(2026, 10, 14, 12, 0), BKK)


def test_day_start_utc():
    assert day_start_utc(date(2026, 10, 15), BKK) == datetime(
        2026, 10, 14, 17, 0, tzinfo=UTC
    )
