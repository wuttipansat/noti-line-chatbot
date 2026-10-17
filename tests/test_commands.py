"""Command parsing. Pure function, no database needed."""

import pytest

from app.bot.commands import Command, parse_command


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("วิธีใช้", Command("help")),
        ("Help", Command("help")),
        ("งานวันนี้", Command("today")),
        (" งาน   7 วัน ", Command("week")),
        ("งานทั้งหมด", Command("all")),
        ("เพิ่มงาน อ่าน paper", Command("add", title="อ่าน paper")),
        ("เพิ่มงานอ่าน paper", Command("add", title="อ่าน paper")),
        ("เพิ่มงาน", Command("add", title="")),
        ("#3 เสร็จ", Command("done", task_no=3)),
        ("#12 เสร็จแล้ว", Command("done", task_no=12)),
        ("#3เสร็จ", Command("done", task_no=3)),
        ("เสร็จ #7", Command("done", task_no=7)),
        ("done 7", Command("done", task_no=7)),
        ("สวัสดี", Command("unknown")),
        ("#abc เสร็จ", Command("unknown")),
    ],
)
def test_parse_command(text, expected):
    assert parse_command(text) == expected
