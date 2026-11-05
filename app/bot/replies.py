"""Turn results into LINE messages (Thai). Pure functions, no database."""

from datetime import date, timedelta
from typing import Any

from app.bot import flex
from app.db.models import Task
from app.services.dates import local_now
from app.services.task_service import TodayView

THAI_WEEKDAYS = ["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]
THAI_MONTHS = [
    "ม.ค.",
    "ก.พ.",
    "มี.ค.",
    "เม.ย.",
    "พ.ค.",
    "มิ.ย.",
    "ก.ค.",
    "ส.ค.",
    "ก.ย.",
    "ต.ค.",
    "พ.ย.",
    "ธ.ค.",
]
PRIORITY_LABELS = {1: "ต่ำ", 3: "สูง"}

MAIN_BUTTONS = [("งานวันนี้", "งานวันนี้"), ("งาน 7 วัน", "งาน 7 วัน"), ("วิธีใช้", "วิธีใช้")]
HELP_TEXT = (
    "ผมช่วยจดงานให้ได้ครับ ลองพิมพ์:\n"
    "• เพิ่มงาน อ่าน paper\n"
    "• พรุ่งนี้บ่ายสองส่งรายงาน\n"
    "• งานวันนี้\n"
    "• งาน 7 วัน\n"
    "• งานทั้งหมด\n"
    "• #3 เสร็จ\n"
    "• ข้อมูลส่วนตัว (ดูว่าเราเก็บอะไรบ้าง)\n"
    "• เลื่อนส่งรายงานเป็นพรุ่งนี้ / ลบงาน 3\n"
    "• ปฏิทิน (ดูงานทั้งเดือน เพิ่มและแก้ไขงาน)\n"
    "• ลบข้อมูลของฉัน"
)

PAST_DATE = "วันนั้นผ่านไปแล้วครับ 🕐 ยังไม่ได้บันทึก\nลองระบุวันใหม่ เช่น: พรุ่งนี้ 9 โมง ประชุม"

PRIVACY_TEXT = (
    "🔒 ข้อมูลที่บอทเก็บ\n"
    "• งานที่คุณจด (ชื่องาน วันเวลา)\n"
    "• ข้อความที่คุณส่ง เพื่อประมวลผลและกันส่งซ้ำ\n"
    "• รหัสผู้ใช้ LINE (ไม่ใช่ชื่อหรือเบอร์โทร)\n\n"
    "ข้อความที่คำสั่งปกติไม่เข้าใจ จะถูกส่งให้ AI ภายนอก"
    "ช่วยตีความ พร้อมรายชื่องานที่ยังไม่เสร็จของคุณ\n\n"
    'พิมพ์ "ลบข้อมูลของฉัน" เพื่อลบทุกอย่างได้ตลอดเวลา'
)

WELCOME_TEXT = "สวัสดีครับ 👋\n" + HELP_TEXT + "\n\n" + PRIVACY_TEXT


def thai_date(day: date) -> str:
    return f"{THAI_WEEKDAYS[day.weekday()]} {day.day} {THAI_MONTHS[day.month - 1]}"


def quick_replies(buttons: list[tuple[str, str]]) -> dict[str, Any]:
    """Buttons under the message. Tapping one sends its text as if typed."""
    return {
        "items": [
            {
                "type": "action",
                "action": {"type": "message", "label": label, "text": text},
            }
            for label, text in buttons
        ]
    }


def text(body: str, buttons: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"type": "text", "text": body}
    if buttons:
        message["quickReply"] = quick_replies(buttons)
    return message


def task_meta(task: Task, tz: str, show_day: bool = False) -> str:
    """The small line under a task: "14:00 · สูง" or "พฤ. 15 ต.ค. 14:00"."""
    parts = []
    if task.due_at is not None:
        local = local_now(task.due_at, tz)
        when = local.strftime("%H:%M")
        parts.append(f"{thai_date(local.date())} {when}" if show_day else when)
    elif task.due_date is not None and show_day:
        parts.append(thai_date(task.due_date))
    if label := PRIORITY_LABELS.get(task.priority):
        parts.append(label)
    return " · ".join(parts)


def task_line(task: Task, tz: str, show_day: bool = False) -> str:
    meta = task_meta(task, tz, show_day)
    return f"#{task.task_no} {task.title}" + (f" · {meta}" if meta else "")


def task_day(task: Task, tz: str) -> date | None:
    """The local day a task is due, or None."""
    if task.due_at is not None:
        return local_now(task.due_at, tz).date()
    return task.due_date


def _clock(task: Task, tz: str) -> str:
    if task.due_at is None:
        return "ทั้งวัน"
    return local_now(task.due_at, tz).strftime("%H:%M")


def _short_day(task: Task, tz: str) -> str:
    """Overdue tasks show their date ("13 ต.ค.") in the time column."""
    day = task_day(task, tz)
    return f"{day.day} {THAI_MONTHS[day.month - 1]}" if day else ""


def _row(task: Task, when: str, overdue: bool = False) -> flex.Row:
    important = task.priority == 3
    return flex.Row(task.task_no, when, task.title, important, overdue)


def today_reply(
    view: TodayView, tz: str, greeting: str | None = None
) -> dict[str, Any]:
    """Today's tasks as a tear-off calendar page (a small page when there is nothing)."""
    day = thai_date(view.local_date)
    if not view.overdue and not view.today:
        return free_day_card(view.local_date)

    count = len(view.overdue) + len(view.today)
    # The notification shows the first lines: lead with the day and the count.
    lines = [f"📋 งานวันนี้ ({day}) มี {count} งาน"]
    if view.overdue:
        lines.append("⚠ เลยกำหนด")
        lines += [task_line(t, tz, show_day=True) for t in view.overdue]
    if view.today:
        lines.append("วันนี้")
        lines += [task_line(t, tz) for t in view.today]
    if greeting:
        lines.insert(0, greeting)

    overdue = [_row(t, _short_day(t, tz), overdue=True) for t in view.overdue]
    sections = [
        flex.Section("เลยกำหนด", overdue, late=True),
        flex.Section(
            "วันนี้" if overdue else "", [_row(t, _clock(t, tz)) for t in view.today]
        ),
    ]
    line = f"{greeting} วันนี้มี {count} งาน" if greeting else None
    mood = "worried" if view.overdue else ("wink" if greeting else "normal")
    buttons = [("งาน 7 วัน", "งาน 7 วัน"), ("งานทั้งหมด", "งานทั้งหมด")]
    return flex.task_card(
        flex.calendar_page(view.local_date, line, mood),
        sections,
        alt_text="\n".join(lines),
        buttons=quick_replies(buttons)["items"],
    )


def calendar_card(liff_url: str) -> dict[str, Any]:
    """A small card with one button that opens the LIFF calendar."""
    return flex.link_card(
        "ปฏิทินงาน",
        "ดูงานทั้งเดือน เพิ่ม แก้ไข หรือลบงานได้ในหน้าเดียว",
        "เปิดปฏิทิน",
        liff_url,
    )


def free_day_card(day: date) -> dict[str, Any]:
    """Nothing to do today: just the page and a happy robot."""
    return flex.note_card(
        flex.calendar_page(day, "ไม่มีงานที่ต้องทำวันนี้", "happy"),
        alt_text=f"📋 งานวันนี้ ({thai_date(day)})\nไม่มีงานที่ต้องทำวันนี้",
        buttons=quick_replies(MAIN_BUTTONS)["items"],
    )


def _relative(day: date, today: date | None) -> str:
    if today is None:
        return ""
    if day < today:
        return "เลยกำหนด"
    return {0: "วันนี้", 1: "พรุ่งนี้"}.get((day - today).days, "")


def _by_day(tasks: list[Task], tz: str, today: date | None) -> list[flex.Section]:
    """Group tasks under a heading per day, like a calendar's agenda view."""
    sections: list[flex.Section] = []
    for task in tasks:
        day = task_day(task, tz)
        label = thai_date(day) if day else "ไม่กำหนดวัน"
        if not sections or sections[-1].label != label:
            if day is None:
                sections.append(flex.Section(label, []))
            else:
                note = _relative(day, today)
                late = today is not None and day < today
                marker = flex.day_colour(day).fill
                sections.append(flex.Section(label, [], marker, note, late))
        sections[-1].rows.append(_row(task, _clock(task, tz) if day else ""))
    return sections


def list_reply(
    heading: str,
    tasks: list[Task],
    tz: str,
    today: date | None = None,
    week: bool = False,
) -> dict[str, Any]:
    """Tasks grouped by day. week=True: the header strip shows the next 7 days."""
    if not tasks:
        return text(f"{heading}\nยังไม่มีงาน", MAIN_BUTTONS)
    lines = [f"{heading} {len(tasks)} งาน"] + [
        task_line(t, tz, show_day=True) for t in tasks
    ]
    _, _, title = heading.partition(" ")  # drop the emoji
    if week and today is not None:
        last = today + timedelta(days=6)
        subtitle = f"{today.day} {THAI_MONTHS[today.month - 1]} ถึง {thai_date(last)}"
        binding = flex.week_binding(today)
    else:
        subtitle = "เรียงตามวันที่ต้องทำ"
        binding = [flex.PERFORATION]
    return flex.task_card(
        flex.list_page(title or heading, subtitle, f"{len(tasks)} งาน", binding),
        _by_day(tasks, tz, today),
        alt_text="\n".join(lines),
        buttons=quick_replies(MAIN_BUTTONS[:2])["items"],
    )
