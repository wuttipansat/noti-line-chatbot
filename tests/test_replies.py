"""Reply formatting. Pure functions: Task objects are built in memory, not saved."""

from datetime import UTC, date, datetime

from app.bot import flex, mascot, replies
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


def rows_of(message: dict) -> list[dict]:
    """The task rows of a card: the boxes that end with a tick box."""
    body = message["contents"]["body"]["contents"]
    return [b for b in body if b["type"] == "box" and "action" in b["contents"][-1]]


def headings_of(message: dict) -> list[dict]:
    body = message["contents"]["body"]["contents"]
    return [b for b in body if b["type"] == "box" and b not in rows_of(message)]


def label(heading: dict) -> str:
    return next(c["text"] for c in heading["contents"] if c.get("weight") == "bold")


def done_text(row: dict) -> str:
    return row["contents"][-1]["action"]["text"]


def when_text(row: dict) -> str:
    return row["contents"][0]["contents"][0]["text"]


def test_today_card_is_a_tear_off_page_in_the_weekday_colour():
    view = TodayView(
        local_date=date(2026, 10, 15),  # a Thursday: orange
        overdue=[task(1, "ส่งใบเบิก", due_date=date(2026, 10, 13))],
        today=[task(2, "อ่าน paper", due_date=date(2026, 10, 15), priority=3)],
    )

    message = replies.today_reply(view, BKK)

    assert message["type"] == "flex"
    assert message["altText"] == (
        "📋 งานวันนี้ (พฤ. 15 ต.ค.) มี 2 งาน\n"
        "⚠ เลยกำหนด\n"
        "#1 ส่งใบเบิก · อ. 13 ต.ค.\n"
        "วันนี้\n"
        "#2 อ่าน paper · สูง"
    )
    binding, printed, perforation = message["contents"]["header"]["contents"]
    thursday = flex.DAY_COLOURS[3]
    assert binding["contents"][0]["backgroundColor"] == thursday.fill
    big_date, side, robot = printed["contents"]
    assert (big_date["text"], big_date["color"]) == ("15", thursday.ink)
    assert side["contents"][0]["text"] == "วันพฤหัสบดี"
    assert side["contents"][1]["text"] == "ตุลาคม 2569"  # Buddhist year
    assert len(perforation["contents"]) == flex.PERFORATION_HOLES
    assert robot == mascot.robot("worried", thursday.fill)  # overdue work: worried

    rows = rows_of(message)
    assert [done_text(r) for r in rows] == ["#1 เสร็จ", "#2 เสร็จ"]
    assert when_text(rows[0]) == "13 ต.ค."  # overdue: its date
    assert when_text(rows[1]) == "ทั้งวัน"  # no time set
    labels = [item["action"]["label"] for item in message["quickReply"]["items"]]
    assert labels == ["งาน 7 วัน", "งานทั้งหมด"]


def test_long_titles_wrap_to_two_lines_at_most():
    long = "เตรียมเอกสารประกอบการประชุมคณะกรรมการประจำไตรมาสและส่งให้ฝ่ายบัญชีตรวจทาน"
    view = TodayView(local_date=date(2026, 10, 15), overdue=[], today=[task(9, long)])

    [row] = rows_of(replies.today_reply(view, BKK))

    title = row["contents"][1]["contents"][0]
    assert (title["text"], title["wrap"], title["maxLines"]) == (long, True, 2)


def test_morning_greeting_goes_on_the_page_and_first_in_alt_text():
    view = TodayView(local_date=date(2026, 10, 15), overdue=[], today=[task(2, "x")])

    message = replies.today_reply(view, BKK, greeting="อรุณสวัสดิ์ครับ")

    assert message["altText"].startswith("อรุณสวัสดิ์ครับ\n📋 งานวันนี้")
    side = message["contents"]["header"]["contents"][1]["contents"][1]
    assert side["contents"][-1]["text"] == "อรุณสวัสดิ์ครับ วันนี้มี 1 งาน"


def eyes_of(message: dict) -> tuple[str, str]:
    """Which mood the robot in a day card shows (matched against mascot.EYES)."""
    robot = message["contents"]["header"]["contents"][1]["contents"][2]
    for mood in mascot.MOODS:
        if robot == mascot.robot(mood, robot["contents"][0]["backgroundColor"]):
            return mood
    raise AssertionError("not a known robot")


def test_robot_mood_follows_the_day():
    one = [task(2, "x")]
    late = [task(1, "y", due_date=date(2026, 10, 13))]
    day = date(2026, 10, 15)

    assert eyes_of(replies.today_reply(TodayView(day, [], one), BKK)) == "normal"
    digest = replies.today_reply(TodayView(day, [], one), BKK, greeting="อรุณสวัสดิ์ครับ")
    assert eyes_of(digest) == "wink"
    assert eyes_of(replies.today_reply(TodayView(day, late, one), BKK)) == "worried"


def test_free_day_is_a_small_page_with_a_happy_robot():
    view = TodayView(local_date=date(2026, 10, 15), overdue=[], today=[])

    message = replies.today_reply(view, BKK)

    assert message["type"] == "flex"
    assert "body" not in message["contents"]
    assert eyes_of(message) == "happy"
    assert message["altText"] == "📋 งานวันนี้ (พฤ. 15 ต.ค.)\nไม่มีงานที่ต้องทำวันนี้"


def test_every_mood_draws_the_same_size_robot():
    for mood in mascot.MOODS:
        robot = mascot.robot(mood, "#123456")
        cap, face, chin = robot["contents"]
        assert cap["backgroundColor"] == chin["backgroundColor"] == "#123456"
        head = face["contents"][1]
        assert (head["width"], head["height"]) == ("52px", "38px")


def test_week_is_grouped_by_day_under_a_seven_colour_binding():
    tasks = [
        task(1, "a", due_at=datetime(2026, 10, 15, 3, 0, tzinfo=UTC)),  # Thu 10:00
        task(2, "b", due_date=date(2026, 10, 15)),
        task(3, "c", due_date=date(2026, 10, 16)),  # Friday
    ]

    message = replies.list_reply(
        "🗓 งาน 7 วันข้างหน้า", tasks, BKK, date(2026, 10, 15), week=True
    )

    headings = headings_of(message)
    assert [label(h) for h in headings] == ["พฤ. 15 ต.ค.", "ศ. 16 ต.ค."]
    assert [h["contents"][-1]["text"] for h in headings] == ["วันนี้", "พรุ่งนี้"]
    assert headings[1]["contents"][0]["backgroundColor"] == flex.DAY_COLOURS[4].fill
    assert [when_text(r) for r in rows_of(message)] == ["10:00", "ทั้งวัน", "ทั้งวัน"]
    binding, text = message["contents"]["header"]["contents"]
    assert binding["contents"][0]["backgroundColor"] == flex.DAY_COLOURS[3].fill
    assert len(binding["contents"]) == 7
    assert text["contents"][1]["text"] == "15 ต.ค. ถึง พ. 21 ต.ค."


def test_all_tasks_marks_past_days_late_and_puts_undated_last():
    tasks = [
        task(1, "late", due_date=date(2026, 10, 13)),
        task(2, "someday"),
    ]

    message = replies.list_reply("📝 งานที่ยังไม่เสร็จ", tasks, BKK, date(2026, 10, 15))

    late, undated = headings_of(message)
    assert label(late) == "อ. 13 ต.ค." and late["contents"][-1]["text"] == "เลยกำหนด"
    assert label(undated) == "ไม่กำหนดวัน"


def test_long_list_shows_12_rows_then_and_more():
    many = [task(i, f"งาน {i}") for i in range(1, 26)]

    message = replies.list_reply("📝 งานที่ยังไม่เสร็จ", many, BKK)

    assert len(rows_of(message)) == flex.MAX_ROWS
    last = message["contents"]["body"]["contents"][-1]
    assert last["text"] == "และอีก 13 งาน"
    title = message["contents"]["header"]["contents"][1]["contents"][0]["contents"][0]
    assert title["text"] == "งานที่ยังไม่เสร็จ"
    assert len(message["altText"]) <= flex.ALT_TEXT_LIMIT
