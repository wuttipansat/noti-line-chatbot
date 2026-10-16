"""The bot's brain (for now, without AI): one incoming event -> one reply.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import replies
from app.bot.commands import parse_command
from app.db.models import InboundEvent, User
from app.services import task_service as tasks
from app.services.outbox_service import queue_reply


async def handle_event(session: AsyncSession, user: User, event: InboundEvent) -> None:
    if event.event_type == "follow":
        welcome = "สวัสดีครับ 👋\n" + replies.HELP_TEXT
        await queue_reply(
            session, user, event, [replies.text(welcome, replies.MAIN_BUTTONS)]
        )
        return

    message = event.payload.get("message") or {}
    if event.event_type != "message" or message.get("type") != "text":
        return

    reply, critical = await _answer(session, user, message["text"], event)
    await queue_reply(session, user, event, [reply], critical=critical)


async def _answer(session, user, text, event) -> tuple[dict, bool]:
    """Returns (LINE message, critical?). Critical = the user must learn the result."""
    command = parse_command(text)
    tz = user.timezone
    now = event.event_ts

    match command.action:
        case "help":
            return replies.text(replies.HELP_TEXT, replies.MAIN_BUTTONS), False

        case "add":
            try:
                task = await tasks.add_task(session, user.id, command.title or "")
            except tasks.InvalidTask:
                return replies.text("กรุณาพิมพ์ชื่องานด้วยครับ เช่น: เพิ่มงาน อ่าน paper"), False
            body = f"✅ เพิ่มงาน #{task.task_no} {task.title}"
            return replies.text(
                body,
                [
                    (f"เสร็จ #{task.task_no}", f"#{task.task_no} เสร็จ"),
                    ("งานทั้งหมด", "งานทั้งหมด"),
                ],
            ), True

        case "done":
            try:
                task = await tasks.complete_task(session, user.id, command.task_no)
            except tasks.TaskNotFound:
                return replies.text(
                    f"ไม่พบงาน #{command.task_no} ครับ", [("งานทั้งหมด", "งานทั้งหมด")]
                ), False
            return replies.text(
                f"✅ #{task.task_no} {task.title} เสร็จแล้ว", [("งานวันนี้", "งานวันนี้")]
            ), True

        case "today":
            view = await tasks.today_view(session, user.id, now)
            return replies.today_reply(view, tz), False

        case "week":
            week = await tasks.week_view(session, user.id, now)
            return replies.list_reply("🗓 งาน 7 วันข้างหน้า", week, tz), False

        case "all":
            open_tasks = await tasks.list_open_tasks(session, user.id)
            return replies.list_reply("📝 งานที่ยังไม่เสร็จ", open_tasks, tz), False

        case _:
            return replies.text(
                'ยังไม่เข้าใจครับ 🙏 ลองพิมพ์ "วิธีใช้"', replies.MAIN_BUTTONS
            ), False