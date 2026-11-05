"""The bot's brain: one incoming event -> one reply.

Order of understanding (cheapest and most predictable first):
0. An answer to the bot's pending question, e.g. tapping [14:00].
1. Fixed commands (parse_command) — free, instant, exact.
2. The language model — only when 1 did not understand, and its answer is
   validated (app/ai/interpreter.py) before it can change anything.

The task change and the reply in the outbox are committed together — or not at
all. The slow part (waiting for the model) happens between two short
transactions, so no database connection or row lock is held while it runs:
  prepare (transaction) -> think (no transaction) -> finish (transaction)
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.interpreter import MAX_COMMANDS, Plan, interpret_plan
from app.ai.mock import MockProvider
from app.ai.provider import ExtractContext, LLMProvider, OpenTask
from app.bot import replies
from app.bot.commands import Command, parse_command
from app.db.models import InboundEvent, User
from app.services import conversation_service as conversation
from app.services import limit_service as limits
from app.services import privacy_service as privacy
from app.services import task_service as tasks
from app.services.dates import local_now, local_today
from app.services.limit_service import Limits
from app.services.outbox_service import queue_reply

SLOW_DOWN = "ส่งข้อความเร็วเกินไปครับ 🙏 รอสักครู่แล้วค่อยส่งใหม่นะครับ"
CONFIRM_FORGET = "ยืนยันลบข้อมูล"
CONFIRM_DELETE = "ยืนยันลบ #{}"
FORGOTTEN = "🗑 ลบข้อมูลทั้งหมดของคุณแล้วครับ\nพิมพ์งานใหม่เมื่อไหร่ก็เริ่มใช้ต่อได้เลย"
MOCK = MockProvider()
DEFAULT_LIMITS = Limits()


@dataclass(frozen=True)
class Ask:
    """The model must be asked before we can act. Everything it needs is here."""

    text: str
    context: ExtractContext
    provider: LLMProvider


class BotHandler:
    """prepare -> think -> finish. Calling it runs all three in one session
    (simple; used by tests). The inbox runs them in separate transactions."""

    def __init__(self, prepare, think, finish):
        self.prepare, self.think, self.finish = prepare, think, finish

    @staticmethod
    def is_slow(step) -> bool:
        """True when `think` has to wait for the model."""
        return isinstance(step, Ask)

    async def __call__(
        self, session: AsyncSession, user: User, event: InboundEvent
    ) -> None:
        step = await self.prepare(session, user, event)
        if self.is_slow(step):
            step = await self.think(step)
        if step is not None:
            await self.finish(session, user, event, step)


def make_handler(
    provider: LLMProvider, limit: Limits = DEFAULT_LIMITS, liff_id: str | None = None
) -> BotHandler:

    liff_url = f"https://liff.line.me/{liff_id}" if liff_id else None

    async def prepare(
        session: AsyncSession, user: User, event: InboundEvent
    ) -> Plan | Ask | None:
        """Everything quick. Returns None (done), a Plan (ready to run) or an Ask."""
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
            return None
        if command is None:
            command = parse_command(text)
        if command.action != "unknown":
            return Plan((command,))
        model = provider
        if not await limits.take_ai_call(
            session, user, event.event_ts, limit.ai_per_day
        ):
            model = MOCK  # today's AI budget is used up: rules only, free
        return Ask(text, await _context(session, user, event), model)

    async def think(ask: Ask) -> Plan:
        """Wait for the model. No database transaction is open here."""
        plan, _ = await interpret_plan(ask.text, ask.context, ask.provider)
        return plan

    async def finish(
        session: AsyncSession, user: User, event: InboundEvent, plan: Plan
    ) -> None:
        if len(plan.commands) == 1 and not plan.skipped:  # the usual case
            reply, critical = await _run(
                session, user, event, plan.commands[0], liff_url
            )
        elif plan.commands:  # several tasks, or some part was not understood
            reply, critical = await _run_batch(session, user, event, plan)
        elif plan.skipped and all(x.past for x in plan.skipped):
            reply, critical = (
                replies.text(replies.PAST_DATE, replies.MAIN_BUTTONS),
                False,
            )
        else:
            reply, critical = await _run(session, user, event, Command("unknown"))
        await queue_reply(session, user, event, [reply], critical=critical)

    return BotHandler(prepare, think, finish)


async def _context(session, user, event) -> ExtractContext:
    open_tasks = await tasks.list_open_tasks(session, user.id)
    sent_at = local_now(event.event_ts, user.timezone)  # when the user wrote it
    return ExtractContext(
        today=sent_at.date(),
        timezone=user.timezone,
        open_tasks=[_open_task(t, user.timezone) for t in open_tasks],
        now=sent_at.time().replace(second=0, microsecond=0),
    )


def _open_task(task, tz: str) -> OpenTask:
    """What the model may know about a task: number, name and current deadline."""
    if task.due_at is not None:
        local = local_now(task.due_at, tz)
        return OpenTask(task.task_no, task.title, local.date(), local.time())
    return OpenTask(task.task_no, task.title, task.due_date)


async def _run_batch(session, user, event, plan: Plan) -> tuple[dict, bool]:
    """Several commands from one message -> ONE reply that says what happened to each.

    Nothing is silent: saved, not found, not understood and over-the-limit all show up.
    """
    tz = user.timezone
    lines: list[str] = []
    saved = 0
    buttons = [("งานวันนี้", "งานวันนี้"), ("งานทั้งหมด", "งานทั้งหมด")]
    for command in plan.commands:
        match command.action:
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
                    lines.append(f"⚠ บันทึก “{command.title}” ไม่ได้")
                    continue
                lines.append("➕ " + replies.task_line(task, tz, show_day=True))
                saved += 1
            case "done":
                try:
                    task = await tasks.complete_task(session, user.id, command.task_no)
                except tasks.TaskNotFound:
                    lines.append(f"⚠ ไม่พบงาน #{command.task_no}")
                    continue
                lines.append(f"✅ #{task.task_no} {task.title} เสร็จแล้ว")
                saved += 1
            case "update":
                try:
                    task = await tasks.change_task(
                        session,
                        user.id,
                        command.task_no,
                        title=command.title,
                        due_date=command.due_date,
                        due_at=command.due_at,
                    )
                except (tasks.TaskNotFound, tasks.InvalidTask):
                    lines.append(f"⚠ แก้ #{command.task_no} ไม่ได้")
                    continue
                lines.append("✏️ " + replies.task_line(task, tz, show_day=True))
                saved += 1
            case "ask_time":  # at most one per message (the interpreter makes sure)
                await conversation.ask_time(session, user.id, event, command)
                day = replies.thai_date(command.due_date)
                lines.append(f"⏰ {command.title} ({day}) กี่โมงครับ?")
                buttons = [(t, t) for t in command.time_options] + [("ยกเลิก", "ยกเลิก")]

    head = f"📝 จัดการให้ {len(plan.commands)} รายการ"
    body = "\n".join([head, *lines])
    past = [x for x in plan.skipped if x.past]
    unclear = [x for x in plan.skipped if not x.too_many and not x.past]
    over = [x for x in plan.skipped if x.too_many]
    if past:
        names = ", ".join(f"“{x.label}”" for x in past if x.label)
        extra = f": {names}" if names else ""
        body += (
            f"\n\n⚠ วันที่ผ่านไปแล้ว {len(past)} รายการ{extra}\nยังไม่ได้บันทึก ลองระบุวันใหม่นะครับ"
        )
    if unclear:
        names = ", ".join(f"“{x.label}”" for x in unclear if x.label)
        extra = f": {names}" if names else ""
        body += f"\n\n⚠ ไม่เข้าใจอีก {len(unclear)} รายการ{extra}\nลองพิมพ์ใหม่แยกทีละงานนะครับ"
    if over:
        names = ", ".join(f"“{x.label}”" for x in over if x.label)
        extra = f": {names}" if names else ""
        body += (
            f"\n\n⚠ ส่งได้ทีละ {MAX_COMMANDS} งานครับ ยังไม่ได้บันทึกอีก {len(over)} งาน{extra}"
            "\nส่งส่วนที่เหลือมาอีกครั้งได้เลย"
        )
    return replies.text(body, buttons), saved > 0


async def _run(
    session, user, event, command: Command, liff_url: str | None = None
) -> tuple[dict, bool]:
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
                    (f"ลบ #{task.task_no}", f"ลบ {task.task_no}"),
                    ("งานทั้งหมด", "งานทั้งหมด"),
                ],
            ), True

        case "update":
            try:
                task = await tasks.change_task(
                    session,
                    user.id,
                    command.task_no,
                    title=command.title,
                    due_date=command.due_date,
                    due_at=command.due_at,
                )
            except tasks.TaskNotFound:
                return replies.text(
                    f"ไม่พบงาน #{command.task_no} ครับ", [("งานทั้งหมด", "งานทั้งหมด")]
                ), False
            except tasks.InvalidTask:
                return replies.text("แก้งานนี้ไม่ได้ครับ ลองพิมพ์ใหม่อีกครั้ง"), False
            return replies.text(
                "✏️ แก้แล้ว " + replies.task_line(task, tz, show_day=True),
                [
                    (f"เสร็จ #{task.task_no}", f"#{task.task_no} เสร็จ"),
                    ("งานทั้งหมด", "งานทั้งหมด"),
                ],
            ), True

        case "delete":
            try:
                task = await tasks.get_task(session, user.id, command.task_no)
            except tasks.TaskNotFound:
                return replies.text(
                    f"ไม่พบงาน #{command.task_no} ครับ", [("งานทั้งหมด", "งานทั้งหมด")]
                ), False
            await conversation.ask_delete_confirmation(
                session, user.id, event, task.task_no, task.title
            )
            confirm = CONFIRM_DELETE.format(task.task_no)
            return replies.text(
                f"🗑 ลบงาน #{task.task_no} {task.title} ใช่ไหมครับ?",
                [(confirm, confirm), ("ยกเลิก", "ยกเลิก")],
            ), False

        case "ask_time":
            await conversation.ask_time(session, user.id, event, command)
            day = replies.thai_date(command.due_date)
            buttons = [(t, t) for t in command.time_options] + [("ยกเลิก", "ยกเลิก")]
            return replies.text(f"⏰ {command.title} ({day}) กี่โมงครับ?", buttons), False

        case "privacy":
            return replies.text(replies.PRIVACY_TEXT, replies.MAIN_BUTTONS), False

        case "calendar":
            if liff_url is None:
                return replies.text("ปฏิทินยังไม่เปิดให้ใช้ครับ", replies.MAIN_BUTTONS), False
            return replies.calendar_card(liff_url), False

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
            today = local_today(now, tz)
            return replies.list_reply(
                "🗓 งาน 7 วันข้างหน้า", week, tz, today, week=True
            ), False

        case "all":
            open_tasks = await tasks.list_open_tasks(session, user.id)
            today = local_today(now, tz)
            return replies.list_reply("📝 งานที่ยังไม่เสร็จ", open_tasks, tz, today), False

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
    if state.kind == "confirm_forget":
        expected = [CONFIRM_FORGET]
    elif state.kind == "confirm_delete":
        expected = [CONFIRM_DELETE.format(state.payload["task_no"])]
    else:
        expected = state.payload["options"]
    if text not in expected:
        return None  # unrelated message: handle it normally
    if event.event_ts > state.expires_at:
        return replies.text(
            "คำถามนั้นหมดเวลาแล้วครับ 🙏 พิมพ์งานใหม่อีกครั้งได้เลย", replies.MAIN_BUTTONS
        )
    if state.kind == "confirm_forget":
        await privacy.forget_user(session, user, event)
        return replies.text(FORGOTTEN)
    if state.kind == "confirm_delete":
        try:
            await tasks.delete_task(session, user.id, state.payload["task_no"])
        except tasks.TaskNotFound:
            return replies.text("ไม่พบงานนี้แล้วครับ", replies.MAIN_BUTTONS)
        title = state.payload["title"]
        return replies.text(
            f"🗑 ลบ #{state.payload['task_no']} {title} แล้ว", [("งานทั้งหมด", "งานทั้งหมด")]
        )
    return conversation.answer_time(state, text, user.timezone)


handle_event = make_handler(MOCK)
