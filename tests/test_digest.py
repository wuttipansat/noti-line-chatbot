"""Daily digest: one morning push per user per day (real PostgreSQL)."""

import asyncio
from datetime import date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.db.models import OutboundMessage, User
from app.services import digest_service as digest
from app.services import task_service as tasks
from tests.helpers import run_committed

BKK = ZoneInfo("Asia/Bangkok")
SEVEN = time(7, 0)
MORNING = datetime(2026, 10, 20, 7, 30, tzinfo=BKK)  # Tue 20 Oct, 07:30 Bangkok


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=BKK)


@pytest.mark.parametrize(
    ("local", "last_sent_on", "due"),
    [
        (at(20, 6, 59), None, False),  # too early
        (at(20, 7, 0), None, True),
        (at(20, 21, 0), None, True),  # the worker was down all morning
        (at(20, 9, 0), date(2026, 10, 20), False),  # already sent today
        (at(21, 7, 5), date(2026, 10, 20), True),  # a new day
    ],
)
def test_digest_due(local, last_sent_on, due):
    assert digest.digest_due(local, SEVEN, last_sent_on) is due


@pytest.fixture
def line_user_id():
    new_id = f"U-digest-{uuid4()}"
    yield new_id

    async def cleanup(sessions):
        async with sessions.begin() as session:
            await session.execute(delete(User).where(User.line_user_id == new_id))

    run_committed(cleanup)


async def make_user(sessions, line_user_id, *, with_task=True) -> int:
    async with sessions.begin() as session:
        user = await tasks.get_or_create_user(session, line_user_id)
        if with_task:
            await tasks.add_task(
                session,
                user.id,
                "ส่งรายงาน",
                due_at=datetime(2026, 10, 20, 14, 0, tzinfo=BKK),
            )
        return user.id


async def queue(sessions, user_id, now) -> bool:
    async with sessions.begin() as session:
        return await digest.queue_digest(session, user_id, now)


async def outbox_rows(sessions, user_id) -> list[OutboundMessage]:
    async with sessions() as session:
        rows = await session.scalars(
            select(OutboundMessage)
            .where(OutboundMessage.user_id == user_id)
            .order_by(OutboundMessage.id)
        )
        return list(rows)


def test_digest_is_queued_once_per_day(line_user_id):
    async def scenario(sessions):
        user_id = await make_user(sessions, line_user_id)

        assert await queue(sessions, user_id, MORNING) is True
        assert await queue(sessions, user_id, MORNING + timedelta(hours=1)) is False

        [row] = await outbox_rows(sessions, user_id)
        assert row.idempotency_key == f"daily:{user_id}:2026-10-20"
        assert row.critical is True
        assert row.reply_token is None
        body = row.messages[0]["text"]
        assert body.startswith("☀️ อรุณสวัสดิ์ครับ\n📋 งานวันนี้ (อ. 20 ต.ค.)")
        assert "#1 ส่งรายงาน · 14:00" in body

    run_committed(scenario)


def test_next_day_gets_a_new_digest(line_user_id):
    async def scenario(sessions):
        user_id = await make_user(sessions, line_user_id)

        await queue(sessions, user_id, MORNING)
        assert await queue(sessions, user_id, MORNING + timedelta(days=1)) is True

        assert len(await outbox_rows(sessions, user_id)) == 2

    run_committed(scenario)


def test_nothing_to_do_means_no_push(line_user_id):
    async def scenario(sessions):
        user_id = await make_user(sessions, line_user_id, with_task=False)

        assert await queue(sessions, user_id, MORNING) is False
        assert await outbox_rows(sessions, user_id) == []
        async with sessions() as session:
            user = await session.get(User, user_id)
        assert user.last_digest_on == date(2026, 10, 20)  # checked: not again today

    run_committed(scenario)


def test_two_workers_at_once_queue_only_one(line_user_id):
    """AC-23: two beat processes by mistake must not double-send."""

    async def scenario(sessions):
        user_id = await make_user(sessions, line_user_id)

        results = await asyncio.gather(
            queue(sessions, user_id, MORNING), queue(sessions, user_id, MORNING)
        )

        assert sorted(results) == [False, True]
        assert len(await outbox_rows(sessions, user_id)) == 1

    run_committed(scenario)


def test_users_due_follows_local_time_and_skips_inactive(line_user_id):
    async def scenario(sessions):
        user_id = await make_user(sessions, line_user_id)

        async with sessions() as session:
            before = await digest.users_due(
                session, MORNING - timedelta(hours=1), SEVEN
            )
            after = await digest.users_due(session, MORNING, SEVEN)
        assert user_id not in before  # 06:30 in Bangkok
        assert user_id in after

        async with sessions.begin() as session:
            (await session.get(User, user_id)).is_active = False
        async with sessions() as session:
            assert user_id not in await digest.users_due(session, MORNING, SEVEN)

    run_committed(scenario)
