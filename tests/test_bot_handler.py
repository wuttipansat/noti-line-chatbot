"""The bot end to end (minus LINE): message in -> task change + reply in outbox.

Runs the real inbox processing against PostgreSQL. Each test cleans up its users.
"""

from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select

from app.bot.handler import handle_event, make_handler
from app.db.models import InboundEvent, OutboundMessage, Task, User
from app.services import task_service as tasks
from app.services.inbox_service import process_user_inbox
from tests.helpers import run_committed

BKK = ZoneInfo("Asia/Bangkok")
NOON = datetime(2026, 10, 15, 12, 0, tzinfo=BKK)


@pytest.fixture
def make_user_id():
    """Call it to get a fresh LINE user id; all of them are deleted afterwards."""
    created: list[str] = []

    def make() -> str:
        created.append(f"U-bot-{uuid4()}")
        return created[-1]

    yield make

    async def cleanup(sessions):
        async with sessions.begin() as session:
            await session.execute(
                delete(InboundEvent).where(InboundEvent.line_user_id.in_(created))
            )
            await session.execute(delete(User).where(User.line_user_id.in_(created)))

    run_committed(cleanup)


async def send(sessions, line_user_id, payload, sent_at, handler=handle_event):
    """Pretend LINE delivered one event, then let the worker process it."""
    async with sessions.begin() as session:
        session.add(
            InboundEvent(
                webhook_event_id=f"evt-{uuid4()}",
                line_user_id=line_user_id,
                event_type=payload["type"],
                event_ts=sent_at,
                reply_token=f"tok-{uuid4()}",
                payload=payload,
            )
        )
    await process_user_inbox(sessions, line_user_id, "w1", handler=handler)


async def say(sessions, line_user_id, text, sent_at=NOON, handler=handle_event):
    message = {"type": "message", "message": {"type": "text", "text": text}}
    await send(sessions, line_user_id, message, sent_at, handler)


async def replies_to(sessions, line_user_id: str) -> list[OutboundMessage]:
    async with sessions() as session:
        rows = await session.scalars(
            select(OutboundMessage)
            .join(User, User.id == OutboundMessage.user_id)
            .where(User.line_user_id == line_user_id)
            .order_by(OutboundMessage.id)
        )
        return list(rows)


def texts(rows: list[OutboundMessage]) -> list[str]:
    return [row.messages[0]["text"] for row in rows]


def test_add_then_done(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(sessions, alice, "#1 เสร็จ", NOON + timedelta(seconds=5))

        rows = await replies_to(sessions, alice)
        assert texts(rows) == ["✅ เพิ่มงาน #1 อ่าน paper", "✅ #1 อ่าน paper เสร็จแล้ว"]
        assert [row.critical for row in rows] == [True, True]
        async with sessions() as session:
            task = await session.scalar(
                select(Task).join(User).where(User.line_user_id == alice)
            )
        assert task.status == "done"

    run_committed(scenario)


def test_done_unknown_task(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "#9 เสร็จ")

        [row] = await replies_to(sessions, alice)
        assert row.messages[0]["text"] == "ไม่พบงาน #9 ครับ"
        assert row.critical is False

    run_committed(scenario)


def test_add_without_title_asks_for_one(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน")

        assert texts(await replies_to(sessions, alice)) == [
            "กรุณาพิมพ์ชื่องานด้วยครับ เช่น: เพิ่มงาน อ่าน paper"
        ]

    run_committed(scenario)


def test_today_uses_the_time_the_user_sent_the_message(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        async with sessions.begin() as session:
            user = await tasks.get_or_create_user(session, alice)
            await tasks.add_task(
                session, user.id, "งานวันที่ 16", due_date=date(2026, 10, 16)
            )

        await say(sessions, alice, "งานวันนี้", datetime(2026, 10, 16, 0, 30, tzinfo=BKK))

        [body] = texts(await replies_to(sessions, alice))
        assert body.startswith("📋 งานวันนี้ (ศ. 16 ต.ค.)")
        assert "#1 งานวันที่ 16" in body

    run_committed(scenario)


def test_users_cannot_complete_each_others_tasks(make_user_id):
    alice, bob = make_user_id(), make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน งานลับของ Alice")
        await say(sessions, bob, "#1 เสร็จ")

        assert texts(await replies_to(sessions, bob)) == ["ไม่พบงาน #1 ครับ"]
        async with sessions() as session:
            task = await session.scalar(
                select(Task).join(User).where(User.line_user_id == alice)
            )
        assert task.status == "todo"

    run_committed(scenario)


def test_unknown_text_gets_a_hint_with_buttons(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "สวัสดีครับ")

        [row] = await replies_to(sessions, alice)
        assert "วิธีใช้" in row.messages[0]["text"]
        assert len(row.messages[0]["quickReply"]["items"]) == 3

    run_committed(scenario)


def test_follow_gets_a_welcome_and_stickers_get_nothing(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await send(sessions, alice, {"type": "follow"}, NOON)
        sticker = {"type": "message", "message": {"type": "sticker", "stickerId": "1"}}
        await send(sessions, alice, sticker, NOON + timedelta(seconds=1))

        [body] = texts(await replies_to(sessions, alice))
        assert body.startswith("สวัสดีครับ 👋")

    run_committed(scenario)


class ScriptedModel:
    """A fake language model that always gives the same answer."""

    name = "scripted"

    def __init__(self, answer: str):
        self.answer = answer

    async def extract(self, text, context):
        return self.answer


def test_natural_language_task_with_deadline(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "พรุ่งนี้บ่ายสองส่งรายงาน")

        [row] = await replies_to(sessions, alice)
        assert row.messages[0]["text"] == "✅ เพิ่มงาน #1 ส่งรายงาน · ศ. 16 ต.ค. 14:00"
        assert row.critical is True

    run_committed(scenario)


def test_bad_model_answer_changes_nothing(make_user_id):
    alice = make_user_id()
    broken = make_handler(ScriptedModel('{"action": "add", "title": "x", "hack": 1}'))

    async def scenario(sessions):
        await say(sessions, alice, "ช่วยจดงานหน่อย", handler=broken)

        assert "ยังไม่เข้าใจ" in texts(await replies_to(sessions, alice))[0]
        async with sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Task)
                .join(User)
                .where(User.line_user_id == alice)
            )
        assert count == 0

    run_committed(scenario)


def test_model_cannot_complete_another_users_task(make_user_id):
    alice, bob = make_user_id(), make_user_id()
    sneaky = make_handler(ScriptedModel('{"action": "done", "task_no": 1}'))

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน งานของ Alice")
        await say(sessions, bob, "ปิดงานแรกให้หน่อย", handler=sneaky)

        assert "ยังไม่เข้าใจ" in texts(await replies_to(sessions, bob))[0]
        async with sessions() as session:
            task = await session.scalar(
                select(Task).join(User).where(User.line_user_id == alice)
            )
        assert task.status == "todo"

    run_committed(scenario)
