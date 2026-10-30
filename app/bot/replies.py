"""Turn results into LINE messages (Thai). Pure functions, no database."""

from datetime import date
from typing import Any

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
    "• ลบข้อมูลของฉัน"
)

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


def task_line(task: Task, tz: str, show_day: bool = False) -> str:
    parts = [f"#{task.task_no} {task.title}"]
    if task.due_at is not None:
        local = local_now(task.due_at, tz)
        when = local.strftime("%H:%M")
        parts.append(f"{thai_date(local.date())} {when}" if show_day else when)
    elif task.due_date is not None and show_day:
        parts.append(thai_date(task.due_date))
    if label := PRIORITY_LABELS.get(task.priority):
        parts.append(label)
    return " · ".join(parts)


def done_buttons(tasks: list[Task]) -> list[tuple[str, str]]:
    return [(f"เสร็จ #{t.task_no}", f"#{t.task_no} เสร็จ") for t in tasks[:3]]


def today_reply(view: TodayView, tz: str) -> dict[str, Any]:
    lines = [f"📋 งานวันนี้ ({thai_date(view.local_date)})"]
    if view.overdue:
        lines.append("⚠ เลยกำหนด")
        lines += [task_line(t, tz, show_day=True) for t in view.overdue]
    if view.today:
        lines.append("วันนี้")
        lines += [task_line(t, tz) for t in view.today]
    if not view.overdue and not view.today:
        lines.append("ไม่มีงานที่ต้องทำวันนี้ 🎉")
    buttons = done_buttons(view.overdue + view.today) + [("งาน 7 วัน", "งาน 7 วัน")]
    return text("\n".join(lines), buttons)


def list_reply(heading: str, tasks: list[Task], tz: str) -> dict[str, Any]:
    if not tasks:
        return text(f"{heading}\nยังไม่มีงาน", MAIN_BUTTONS)
    lines = [heading] + [task_line(t, tz, show_day=True) for t in tasks[:20]]
    if len(tasks) > 20:
        lines.append(f"…และอีก {len(tasks) - 20} งาน")
    return text("\n".join(lines), done_buttons(tasks) + MAIN_BUTTONS[:1])
