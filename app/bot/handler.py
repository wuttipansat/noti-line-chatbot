"""The bot's brain: one incoming event -> one reply.

Order of understanding (cheapest and most predictable first):
1. Fixed commands (parse_command) — free, instant, exact.
2. The language model — only when 1 did not understand, and its answer is
   validated (app/ai/interpreter.py) before it can change anything.

Runs inside the inbox transaction, so the task change and the
reply in the outbox are committed together — or not at all.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.interpreter import interpret
from app.ai.mock import MockProvider
from app.ai.provider import ExtractContext, LLMProvider, OpenTask
from app.bot import replies
from app.bot.commands import Command, parse_command
from app.db.models import InboundEvent, User
from app.services import task_service as tasks
from app.services.dates import local_today
from app.services.inbox_service import Handler
from app.services.outbox_service import queue_reply


def make_handler(provider: LLMProvider) -> Handler:
    async def handle_event(
        session: AsyncSession, user: User, event: InboundEvent
    ) -> None:
        if event.event_type == "follow":
            welcome = "สวัสดีครับ 👋\n" + replies.HELP_TEXT
            await queue_reply(
                session, user, event, [replies.text(welcome, replies.MAIN_BUTTONS)]
            )
            return

        message = event.payload.get("message") or {}
        if event.event_type != "message" or message.get("type") != "text":
            return  # stickers, images, ... are ignored for now

        command = parse_command(message["text"])
        if command.action == "unknown":
            command = await _ask_model(session, user, event, message["text"], provider)
        reply, critical = await _run(session, user, event, command)
        await queue_reply(session, user, event, [reply], critical=critical)

    return handle_event


async def _ask_model(session, user, event, text, provider) -> Command:
    open_tasks = await tasks.list_open_tasks(session, user.id)
    context = ExtractContext(
        today=local_today(event.event_ts, user.timezone),
        timezone=user.timezone,
        open_tasks=[OpenTask(t.task_no, t.title) for t in open_tasks],
    )
    return await interpret(text, context, provider) or Command("unknown")


async def _run(session, user, event, command: Command) -> tuple[dict, bool]:
    """Returns (LINE message, critical?). Critical = the user must learn the result."""
    tz = user.timezone
    now = event.event_ts  # "today" = when the user sent it, not when we process it

    match command.action:
        case "help":
            return replies.text(replies.HELP_TEXT, replies.MAIN_BUTTONS), False

        case "add":
            try:
                task = await tasks.add_task(
                    session,
                    user.id,
                    command.title or "",
                    due_date=command.due_date,
                    due_at=command.due_at,
                    priority=command.priority,
                    estimated_minutes=command.estimated_minutes,
                )
            except tasks.InvalidTask:
                return replies.text("กรุณาพิมพ์ชื่องานด้วยครับ เช่น: เพิ่มงาน อ่าน paper"), False
            body = "✅ เพิ่มงาน " + replies.task_line(task, tz, show_day=True)
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


handle_event = make_handler(MockProvider())
