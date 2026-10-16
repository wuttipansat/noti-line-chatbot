"""Reply formatting. Pure functions: Task objects are built in memory, not saved."""

from datetime import UTC, date, datetime

from app.bot import replies
from app.db.models import Task
from app.services.task_service import TodayView

BKK = "Asia/Bangkok"


def task(no: int, title: str, **fields) -> Task:
    return Task(task_no=no, title=title, priority=fields.pop("priority", 2), **fields)


def test_thai_date():
    assert replies.thai_date(date(2026, 10, 15)) == "พฤ. 15 ต.ค."


def test_task_line_shows_local_time_and_priority():
    t = task(4, "ส่งรายงาน", due_at=datetime(2026, 10, 15, 7, 0, tzinfo=UTC), priority=3)

    assert replies.task_line(t, BKK) == "#4 ส่งรายงาน · 14:00 · สูง"
    assert (
        replies.task_line(t, BKK, show_day=True)
        == "#4 ส่งรายงาน · พฤ. 15 ต.ค. 14:00 · สูง"
    )


def test_today_reply_lists_overdue_first_with_buttons():
    view = TodayView(
        local_date=date(2026, 10, 15),
        overdue=[task(1, "ส่งใบเบิก", due_date=date(2026, 10, 13))],
        today=[task(2, "อ่าน paper", due_date=date(2026, 10, 15))],
    )

    message = replies.today_reply(view, BKK)

    assert message["text"] == (
        "📋 งานวันนี้ (พฤ. 15 ต.ค.)\n"
        "⚠ เลยกำหนด\n"
        "#1 ส่งใบเบิก · อ. 13 ต.ค.\n"
        "วันนี้\n"
        "#2 อ่าน paper"
    )
    labels = [item["action"]["label"] for item in message["quickReply"]["items"]]
    assert labels == ["เสร็จ #1", "เสร็จ #2", "งาน 7 วัน"]
    assert message["quickReply"]["items"][0]["action"]["text"] == "#1 เสร็จ"


def test_empty_today():
    view = TodayView(local_date=date(2026, 10, 15), overdue=[], today=[])

    assert "ไม่มีงานที่ต้องทำวันนี้" in replies.today_reply(view, BKK)["text"]


def test_long_list_is_cut_at_20():
    many = [task(i, f"งาน {i}") for i in range(1, 26)]

    body = replies.list_reply("📝 งานที่ยังไม่เสร็จ", many, BKK)["text"]

    assert "#20 งาน 20" in body
    assert "#21 งาน 21" not in body
    assert body.endswith("…และอีก 5 งาน")