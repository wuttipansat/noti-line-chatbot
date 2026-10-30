"""The bot's brain: one incoming event -> one reply.

Order of understanding (cheapest and most predictable first):
0. An answer to the bot's pending question, e.g. tapping [14:00].
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
from app.services import conversation_service as conversation
from app.services import limit_service as limits
from app.services import privacy_service as privacy
from app.services import task_service as tasks
from app.services.dates import local_today
from app.services.inbox_service import Handler
from app.services.limit_service import Limits
from app.services.outbox_service import queue_reply

SLOW_DOWN = "ส่งข้อความเร็วเกินไปครับ 🙏 รอสักครู่แล้วค่อยส่งใหม่นะครับ"
CONFIRM_FORGET = "ยืนยันลบข้อมูล"
FORGOTTEN = "🗑 ลบข้อมูลทั้งหมดของคุณแล้วครับ\nพิมพ์งานใหม่เมื่อไหร่ก็เริ่มใช้ต่อได้เลย"
MOCK = MockProvider()
DEFAULT_LIMITS = Limits()


def make_handler(provider: LLMProvider, limit: Limits = DEFAULT_LIMITS) -> Handler:
    async def handle_event(
        session: AsyncSession, user: User, event: InboundEvent
    ) -> None:
        if event.event_type == "unfollow":  # the user blocked the bot
            await privacy.set_active(session, user, False)
            return
        if event.event_type == "follow":  # added (or unblocked) the bot
            await privacy.set_active(session, user, True)
            welcome = replies.text(replies.WELCOME_TEXT, replies.MAIN_BUTTONS)
            await queue_reply(session, user, event, [welcome])
            return

        message = event.payload.get("message") or {}
        if event.event_type != "message" or message.get("type") != "text":
            return  # stickers, images, ... are ignored for now

        sent = await limits.messages_in_window(session, user, event, limit.window)
        if sent > limit.messages:
            if sent == limit.messages + 1:  # warn once per burst, then stay quiet
                await queue_reply(session, user, event, [replies.text(SLOW_DOWN)])
            return

        text = message["text"].strip()
        command = await _answer_pending(session, user, event, text)
        if isinstance(command, dict):  # the answer itself needs a reply only
            await queue_reply(session, user, event, [command])
            return
        if command is None:
            command = parse_command(text)
        if command.action == "unknown":
            model = provider
            if not await limits.take_ai_call(
                session, user, event.event_ts, limit.ai_per_day
            ):
                model = MOCK  # today's AI budget is used up: rules only, free
            command = await _ask_model(session, user, event, text, model)
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

        case "ask_time":
            await conversation.ask_time(session, user.id, event, command)
            day = replies.thai_date(command.due_date)
            buttons = [(t, t) for t in command.time_options] + [("ยกเลิก", "ยกเลิก")]
            return replies.text(f"⏰ {command.title} ({day}) กี่โมงครับ?", buttons), False

        case "privacy":
            return replies.text(replies.PRIVACY_TEXT, replies.MAIN_BUTTONS), False

        case "forget_me":
            await conversation.ask_forget_confirmation(session, user.id, event)
            body = (
                "⚠ จะลบงานทั้งหมดและประวัติข้อความของคุณถาวร กู้คืนไม่ได้\n"
                f'ถ้าแน่ใจ กด "{CONFIRM_FORGET}"'
            )
            buttons = [(CONFIRM_FORGET, CONFIRM_FORGET), ("ยกเลิก", "ยกเลิก")]
            return replies.text(body, buttons), False

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


async def _answer_pending(session, user, event, text) -> Command | dict | None:
    """If the bot asked something, is this message the answer?

    Returns a Command to run, a reply (dict) to send, or None = not an answer.
    Any message clears the question: a new topic means the user moved on.
    """
    state = await conversation.pop_state(session, user.id)
    if state is None:
        return None
    if text == "ยกเลิก":
        return replies.text("ยกเลิกแล้วครับ", replies.MAIN_BUTTONS)
    expected = (
        [CONFIRM_FORGET] if state.kind == "confirm_forget" else state.payload["options"]
    )
    if text not in expected:
        return None  # unrelated message: handle it normally
    if event.event_ts > state.expires_at:
        return replies.text(
            "คำถามนั้นหมดเวลาแล้วครับ 🙏 พิมพ์งานใหม่อีกครั้งได้เลย", replies.MAIN_BUTTONS
        )
    if state.kind == "confirm_forget":
        await privacy.forget_user(session, user, event)
        return replies.text(FORGOTTEN)
    return conversation.answer_time(state, text, user.timezone)


handle_event = make_handler(MOCK)
