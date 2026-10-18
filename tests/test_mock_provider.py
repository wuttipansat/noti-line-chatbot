"""The rule-based mock model. No database, no network."""

import asyncio
import json
from datetime import date

import pytest

from app.ai.mock import MockProvider
from app.ai.provider import ExtractContext

CONTEXT = ExtractContext(
    today=date(2026, 10, 15), timezone="Asia/Bangkok", open_tasks=[]
)


def extract(text: str) -> dict:
    return json.loads(asyncio.run(MockProvider().extract(text, CONTEXT)))


@pytest.mark.parametrize(
    ("text", "title", "due_date", "due_time"),
    [
        ("พรุ่งนี้บ่ายสองส่งรายงาน", "ส่งรายงาน", "2026-10-16", "14:00"),
        ("ประชุมทีม 20/10/69 09:30", "ประชุมทีม", "2026-10-20", "09:30"),
        ("วันนี้ 17:00 โทรหาลูกค้า", "โทรหาลูกค้า", "2026-10-15", "17:00"),
        ("เตือนให้จ่ายค่าไฟพรุ่งนี้ด้วยนะ", "จ่ายค่าไฟ", "2026-10-16", None),
        ("เที่ยงกินข้าวกับแม่", "กินข้าวกับแม่", "2026-10-15", "12:00"),
        ("15 พ.ย. ส่ง proposal", "ส่ง proposal", "2026-11-15", None),
    ],
)
def test_understands_tasks_with_a_date_or_time(text, title, due_date, due_time):
    assert extract(text) == {
        "action": "add",
        "title": title,
        "due_date": due_date,
        "due_time": due_time,
    }


@pytest.mark.parametrize("text", ["สวัสดีครับ", "พรุ่งนี้", "31/02/69 งาน", "อ่าน paper"])
def test_everything_else_is_unknown(text):
    assert extract(text) == {"action": "unknown"}


@pytest.mark.parametrize("text", ["สวัสดีครับ", "พรุ่งนี้", "31/02/69 งาน", "อ่าน paper"])
def test_everything_else_is_unknown(text):
    assert extract(text) == {"action": "unknown"}


@pytest.mark.parametrize(
    ("text", "due_time"),
    [
        ("สองโมงประชุมทีม", "14:00"),
        ("ประชุมทีม 3 โมง", "15:00"),
        ("บ่ายสองโมงประชุมทีม", "14:00"),
    ],
)
def test_hours_one_to_five_mean_afternoon(text, due_time):
    answer = extract(text)
    assert answer["due_time"] == due_time
    assert "time_options" not in answer


def test_six_oclock_is_ambiguous():
    assert extract("หกโมงประชุมทีม") == {
        "action": "add",
        "title": "ประชุมทีม",
        "due_date": "2026-10-15",
        "due_time": None,
        "time_options": ["06:00", "18:00"],
    }


def test_ten_oclock_is_not_guessed():
    assert "time_options" not in extract("ประชุมทีม 10 โมง")