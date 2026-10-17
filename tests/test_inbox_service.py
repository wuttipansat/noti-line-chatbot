import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.db.models import InboundEvent, User
from app.services import inbox_service as inbox
from app.services.task_service import get_or_create_user
from tests.helpers import run_committed

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


@pytest.fixture
def line_user_id():
    new_id = f"U-inbox-{uuid4()}"
    yield new_id

    async def cleanup(sessions):
        async with sessions.begin() as session:
            await session.execute(
                delete(InboundEvent).where(InboundEvent.line_user_id == new_id)
            )
            await session.execute(delete(User).where(User.line_user_id == new_id))

    run_committed(cleanup)


async def add_event(sessions, line_user_id: str, text: str, sent_at: datetime):
    async with sessions.begin() as session:
        session.add(
            InboundEvent(
                webhook_event_id=f"evt-{uuid4()}",
                line_user_id=line_user_id,
                event_type="message",
                event_ts=sent_at,
                payload={"type": "message", "message": {"type": "text", "text": text}},
            )
        )


async def statuses(sessions, line_user_id: str) -> dict[str, str]:
    async with sessions() as session:
        events = await session.scalars(
            select(InboundEvent).where(InboundEvent.line_user_id == line_user_id)
        )
        return {e.payload["message"]["text"]: e.status for e in events}


def recorder():
    seen: list[str] = []

    async def handler(session, user, event):
        seen.append(event.payload["message"]["text"])

    return handler, seen


def test_processes_events_in_the_order_the_user_sent_them(line_user_id):
    async def scenario(sessions):
        await add_event(sessions, line_user_id, "second", T0 + timedelta(seconds=2))
        await add_event(sessions, line_user_id, "first", T0)
        await add_event(sessions, line_user_id, "third", T0 + timedelta(seconds=3))
        handler, seen = recorder()

        handled = await inbox.process_user_inbox(
            sessions, line_user_id, owner="w1", handler=handler
        )

        assert handled == 3
        assert seen == ["first", "second", "third"]
        assert set((await statuses(sessions, line_user_id)).values()) == {"done"}

    run_committed(scenario)


def test_failed_event_does_not_block_the_rest(line_user_id):
    async def scenario(sessions):
        for i, text in enumerate(["ok-1", "boom", "ok-2"]):
            await add_event(sessions, line_user_id, text, T0 + timedelta(seconds=i))

        async def handler(session, user, event):
            if event.payload["message"]["text"] == "boom":
                raise RuntimeError("handler crashed")

        await inbox.process_user_inbox(
            sessions, line_user_id, owner="w1", handler=handler
        )

        assert await statuses(sessions, line_user_id) == {
            "ok-1": "done",
            "boom": "failed",
            "ok-2": "done",
        }

    run_committed(scenario)


def test_second_worker_backs_off_while_lease_is_held(line_user_id):
    async def scenario(sessions):
        await add_event(sessions, line_user_id, "hello", T0)
        async with sessions.begin() as session:
            user = await get_or_create_user(session, line_user_id)
            assert await inbox.acquire_lease(session, user.id, "w1", T0)

        handler, seen = recorder()
        handled = await inbox.process_user_inbox(
            sessions, line_user_id, owner="w2", handler=handler, now=lambda: T0
        )

        assert handled == 0
        assert seen == []
        assert await statuses(sessions, line_user_id) == {"hello": "received"}

    run_committed(scenario)


def test_expired_lease_can_be_taken_over(line_user_id):
    async def scenario(sessions):
        await add_event(sessions, line_user_id, "hello", T0)
        async with sessions.begin() as session:
            user = await get_or_create_user(session, line_user_id)
            await inbox.acquire_lease(session, user.id, "crashed-worker", T0)

        later = T0 + timedelta(seconds=inbox.LEASE_SECONDS + 1)
        handler, seen = recorder()
        await inbox.process_user_inbox(
            sessions, line_user_id, owner="w2", handler=handler, now=lambda: later
        )

        assert seen == ["hello"]

    run_committed(scenario)


def test_lease_is_released_after_processing(line_user_id):
    async def scenario(sessions):
        await add_event(sessions, line_user_id, "hello", T0)
        handler, _ = recorder()

        await inbox.process_user_inbox(
            sessions, line_user_id, owner="w1", handler=handler
        )

        async with sessions() as session:
            user = await session.scalar(
                select(User).where(User.line_user_id == line_user_id)
            )
        assert user.inbox_lease_owner is None
        assert user.inbox_lease_until is None

    run_committed(scenario)


def test_two_workers_at_once_handle_each_event_once_in_order(line_user_id):
    async def scenario(sessions):
        texts = [f"msg-{i}" for i in range(6)]
        for i, text in enumerate(texts):
            await add_event(sessions, line_user_id, text, T0 + timedelta(seconds=i))
        seen: list[str] = []

        async def slow_handler(session, user, event):
            await asyncio.sleep(0.01)
            seen.append(event.payload["message"]["text"])

        await asyncio.gather(
            inbox.process_user_inbox(sessions, line_user_id, "w1", slow_handler),
            inbox.process_user_inbox(sessions, line_user_id, "w2", slow_handler),
        )

        assert seen == texts

    run_committed(scenario)


def test_sweeper_finds_users_with_old_waiting_events(line_user_id):
    async def scenario(sessions):
        await add_event(sessions, line_user_id, "hello", T0)
        real_now = datetime.now(UTC)

        async with sessions() as session:
            fresh = await inbox.stale_line_user_ids(session, real_now)
            later = await inbox.stale_line_user_ids(
                session, real_now + inbox.STALE_AFTER + timedelta(seconds=5)
            )

        assert line_user_id not in fresh
        assert line_user_id in later

    run_committed(scenario)
