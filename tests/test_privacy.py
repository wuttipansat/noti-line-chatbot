"""Personal data: delete on request (AC-29), unfollow, no user text in logs (AC-27)."""

import logging
from datetime import timedelta

from sqlalchemy import func, select

from app.bot.handler import CONFIRM_FORGET, FORGOTTEN
from app.db.models import InboundEvent, OutboundMessage, Task, User
from tests.helpers import run_committed
from tests.test_bot_handler import NOON, replies_to, say, send, texts


async def count(sessions, model, *where) -> int:
    async with sessions() as session:
        return await session.scalar(
            select(func.count()).select_from(model).where(*where)
        )


async def tasks_of(sessions, line_user_id) -> list[Task]:
    async with sessions() as session:
        rows = await session.scalars(
            select(Task).join(User).where(User.line_user_id == line_user_id)
        )
        return list(rows)


async def events_of(sessions, line_user_id) -> list[InboundEvent]:
    async with sessions() as session:
        rows = await session.scalars(
            select(InboundEvent).where(InboundEvent.line_user_id == line_user_id)
        )
        return list(rows)


def test_forget_me_deletes_everything_after_confirmation(make_user_id):
    alice, bob = make_user_id(), make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(sessions, alice, "เพิ่มงาน ส่งรายงาน", NOON + timedelta(seconds=1))
        await say(sessions, bob, "เพิ่มงาน งานของบ็อบ", NOON + timedelta(seconds=1))

        await say(sessions, alice, "ลบข้อมูลของฉัน", NOON + timedelta(seconds=2))
        question = (await replies_to(sessions, alice))[-1]
        assert question.messages[0]["text"].startswith("⚠ จะลบงานทั้งหมด")
        assert len(await tasks_of(sessions, alice)) == 2  # nothing deleted yet

        await say(sessions, alice, CONFIRM_FORGET, NOON + timedelta(seconds=3))

        assert await tasks_of(sessions, alice) == []
        assert texts(await replies_to(sessions, alice)) == [FORGOTTEN]  # old ones gone
        [event] = await events_of(sessions, alice)
        assert event.payload == {"type": "message"}  # the text is wiped
        assert [t.title for t in await tasks_of(sessions, bob)] == ["งานของบ็อบ"]

        await say(sessions, alice, "เพิ่มงาน เริ่มใหม่", NOON + timedelta(seconds=4))
        assert texts(await replies_to(sessions, alice))[-1] == "✅ เพิ่มงาน #1 เริ่มใหม่"

    run_committed(scenario)


def test_cancel_keeps_the_data(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(sessions, alice, "ลบข้อมูลของฉัน", NOON + timedelta(seconds=1))
        await say(sessions, alice, "ยกเลิก", NOON + timedelta(seconds=2))
        await say(sessions, alice, CONFIRM_FORGET, NOON + timedelta(seconds=3))

        assert len(await tasks_of(sessions, alice)) == 1

    run_committed(scenario)


def test_late_confirmation_deletes_nothing(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(sessions, alice, "ลบข้อมูลของฉัน", NOON + timedelta(seconds=1))
        await say(sessions, alice, CONFIRM_FORGET, NOON + timedelta(minutes=11))

        assert len(await tasks_of(sessions, alice)) == 1

    run_committed(scenario)


def test_unfollow_stops_digests_and_follow_welcomes_back(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        active = (User.line_user_id == alice, User.is_active)
        await send(sessions, alice, {"type": "unfollow"}, NOON)
        assert await count(sessions, User, *active) == 0

        await send(sessions, alice, {"type": "follow"}, NOON + timedelta(minutes=1))
        assert await count(sessions, User, *active) == 1
        welcome = texts(await replies_to(sessions, alice))[-1]
        assert "🔒 ข้อมูลที่บอทเก็บ" in welcome

    run_committed(scenario)


def test_logs_never_contain_what_users_wrote(make_user_id, caplog):
    """AC-27: process a message with logging fully on; its text must not appear."""
    alice = make_user_id()
    secret = "นัดหมอที่โรงพยาบาลรามา"

    async def scenario(sessions):
        with caplog.at_level(logging.DEBUG):
            await say(sessions, alice, f"พรุ่งนี้บ่ายสอง{secret}")
        assert await count(sessions, OutboundMessage) > 0

    run_committed(scenario)
    assert secret not in caplog.text
