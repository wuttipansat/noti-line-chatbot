"""Print sample cards as JSON, to paste into LINE's Flex Message Simulator.

Run:  python -m scripts.flex_preview [today|digest|week|all]   (default: digest)
Then: https://developers.line.biz/flex-simulator/ -> View as JSON -> paste -> Apply
"""

import json
import sys
from datetime import UTC, date, datetime

from app.bot import replies
from app.db.models import Task
from app.services.task_service import TodayView

TZ = "Asia/Bangkok"
TODAY = date(2026, 10, 31)
LONG = "เตรียมเอกสารประกอบการประชุมคณะกรรมการประจำไตรมาสและส่งให้ฝ่ายบัญชีตรวจทาน"


def task(no: int, title: str, priority: int = 2, **due) -> Task:
    return Task(task_no=no, title=title, priority=priority, **due)


def at(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    """Bangkok wall-clock time -> UTC."""
    return datetime(2026, month, day, hour - 7, minute, tzinfo=UTC)


VIEW = TodayView(
    local_date=TODAY,
    overdue=[task(1, "ส่งใบเบิกค่าเดินทาง", due_date=date(2026, 10, 29))],
    today=[
        task(3, "ประชุมทีม", 3, due_at=at(31, 10)),
        task(5, "ซื้อของเข้าบ้าน", due_at=at(31, 17, 30)),
        task(4, "อ่าน paper บทที่ 2", due_date=TODAY),
        task(9, LONG, due_date=TODAY),
    ],
)
WEEK = [
    task(3, "ประชุมทีม", 3, due_at=at(31, 10)),
    task(4, "อ่าน paper บทที่ 2", due_date=TODAY),
    task(6, "โทรนัดช่างแอร์", due_at=at(2, 9, month=11)),
    task(7, "ส่งรายงานประจำเดือน", 3, due_date=date(2026, 11, 4)),
    task(8, "ไปรับพัสดุ", due_at=at(4, 17, month=11)),
]
ALL = [
    task(1, "ส่งใบเบิกค่าเดินทาง", due_date=date(2026, 10, 29)),
    *WEEK,
    task(10, "ต่อทะเบียนรถ", due_date=date(2026, 11, 20)),
    task(11, "หาของขวัญวันเกิดแม่"),
]


def card(name: str) -> dict:
    if name == "today":
        return replies.today_reply(VIEW, TZ)
    if name == "week":
        return replies.list_reply("🗓 งาน 7 วันข้างหน้า", WEEK, TZ, TODAY, week=True)
    if name == "all":
        return replies.list_reply("📝 งานที่ยังไม่เสร็จ", ALL, TZ, TODAY)
    return replies.today_reply(VIEW, TZ, greeting="อรุณสวัสดิ์ครับ")


def main() -> None:
    message = card(sys.argv[1] if len(sys.argv) > 1 else "digest")
    print(json.dumps(message["contents"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
