"""Outbox delivery rules against real PostgreSQL, with a fake LINE client.

Rows are committed (like production); each test cleans up its own user.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import delete, select

from app.db.models import InboundEvent, OutboundMessage, User
from app.services import outbox_service as outbox
from app.services.handlers import acknowledge
from app.services.inbox_service import process_user_inbox
from app.services.task_service import get_or_create_user
from tests.helpers import run_committed


class FakeLine:
    """Stands in for LineClient. Give it the answers LINE should return, in order.

    An answer is an HTTP status code, or an exception to raise (no answer at all).
    """

    def __init__(self, reply=(), push=(), delay: float = 0):
        self.reply_answers = list(reply)
        self.push_answers = list(push)
        self.delay = delay
        self.calls: list[tuple] = []

    async def _answer(self, answers):
        await asyncio.sleep(self.delay)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def reply(self, reply_token, messages):
        self.calls.append(("reply", reply_token))
        return await self._answer(self.reply_answers)

    async def push(self, to, messages, retry_key):
        self.calls.append(("push", to, retry_key))
        return await self._answer(self.push_answers)


def no_answer():
    return httpx2.ReadTimeout("LINE did not answer")


@pytest.fixture
def line_user_id():
    new_id = f"U-outbox-{uuid4()}"
    yield new_id

    async def cleanup(sessions):
        async with sessions.begin() as session:
            await session.execute(
                delete(InboundEvent).where(InboundEvent.line_user_id == new_id)
            )
            await session.execute(delete(User).where(User.line_user_id == new_id))

    run_committed(cleanup)


async def add_text_event(sessions, line_user_id: str, text: str) -> int:
    async with sessions.begin() as session:
        event = InboundEvent(
            webhook_event_id=f"evt-{uuid4()}",
            line_user_id=line_user_id,
            event_type="message",
            event_ts=datetime.now(UTC),
            reply_token=f"tok-{uuid4()}",
            payload={"type": "message", "message": {"type": "text", "text": text}},
        )
        session.add(event)
        await session.flush()
        return event.id


async def make_outbox(sessions, line_user_id: str, *, critical: bool) -> int:
    event_id = await add_text_event(sessions, line_user_id, "hi")
    async with sessions.begin() as session:
        user = await get_or_create_user(session, line_user_id)
        event = await session.get(InboundEvent, event_id)
        await outbox.queue_reply(
            session, user, event, [outbox.text_message("ok")], critical=critical
        )
    async with sessions() as session:
        return await session.scalar(
            select(OutboundMessage.id).where(
                OutboundMessage.idempotency_key == f"reply:{event_id}"
            )
        )


async def load(sessions, outbox_id: int) -> OutboundMessage:
    async with sessions() as session:
        return await session.get(OutboundMessage, outbox_id)


def now() -> datetime:
    return datetime.now(UTC)


def test_reply_success_is_final(line_user_id):
    async def scenario(sessions):
        outbox_id = await make_outbox(sessions, line_user_id, critical=False)
        line = FakeLine(reply=[200])

        status = await outbox.deliver(sessions, outbox_id, line, now())

        row = await load(sessions, outbox_id)
        assert status == "sent"
        assert row.channel_used == "reply"
        assert row.sent_at is not None
        assert [call[0] for call in line.calls] == ["reply"]

    run_committed(scenario)


def test_info_reply_rejected_is_dead_without_push(line_user_id):
    async def scenario(sessions):
        outbox_id = await make_outbox(sessions, line_user_id, critical=False)
        line = FakeLine(reply=[400])

        status = await outbox.deliver(sessions, outbox_id, line, now())

        assert status == "dead"
        assert [call[0] for call in line.calls] == ["reply"]

    run_committed(scenario)


def test_info_reply_without_answer_is_unknown_and_not_retried(line_user_id):
    async def scenario(sessions):
        outbox_id = await make_outbox(sessions, line_user_id, critical=False)
        line = FakeLine(reply=[no_answer()])

        first = await outbox.deliver(sessions, outbox_id, line, now())
        later = now() + timedelta(minutes=5)
        second = await outbox.deliver(sessions, outbox_id, line, later)

        assert first == "unknown"
        assert second == "skipped"
        assert len(line.calls) == 1

    run_committed(scenario)


def test_critical_falls_back_to_push_when_reply_fails(line_user_id):
    async def scenario(sessions):
        outbox_id = await make_outbox(sessions, line_user_id, critical=True)
        line = FakeLine(reply=[400], push=[200])

        status = await outbox.deliver(sessions, outbox_id, line, now())

        row = await load(sessions, outbox_id)
        assert status == "sent"
        assert row.channel_used == "push"
        assert [call[0] for call in line.calls] == ["reply", "push"]
        assert line.calls[1][1] == line_user_id

    run_committed(scenario)


def test_late_critical_skips_reply_and_pushes(line_user_id):
    async def scenario(sessions):
        outbox_id = await make_outbox(sessions, line_user_id, critical=True)
        line = FakeLine(push=[200])

        late = now() + timedelta(minutes=2)
        status = await outbox.deliver(sessions, outbox_id, line, late)

        assert status == "sent"
        assert [call[0] for call in line.calls] == ["push"]

    run_committed(scenario)


def test_late_info_is_dropped_to_save_push_quota(line_user_id):
    async def scenario(sessions):
        outbox_id = await make_outbox(sessions, line_user_id, critical=False)
        line = FakeLine()

        late = now() + timedelta(minutes=2)
        status = await outbox.deliver(sessions, outbox_id, line, late)

        assert status == "dead"
        assert line.calls == []

    run_committed(scenario)


def test_push_retry_reuses_retry_key_and_409_means_sent(line_user_id):
    async def scenario(sessions):
        outbox_id = await make_outbox(sessions, line_user_id, critical=True)
        line = FakeLine(push=[500, 409])
        t1 = now() + timedelta(minutes=2)

        first = await outbox.deliver(sessions, outbox_id, line, t1)
        too_soon = await outbox.deliver(sessions, outbox_id, line, t1)
        second = await outbox.deliver(
            sessions, outbox_id, line, t1 + timedelta(seconds=6)
        )

        assert (first, too_soon, second) == ("pending", "skipped", "sent")
        first_key, second_key = line.calls[0][2], line.calls[1][2]
        assert first_key == second_key

    run_committed(scenario)


def test_push_client_error_is_dead(line_user_id):
    async def scenario(sessions):
        outbox_id = await make_outbox(sessions, line_user_id, critical=True)
        line = FakeLine(push=[400])

        late = now() + timedelta(minutes=2)
        status = await outbox.deliver(sessions, outbox_id, line, late)

        row = await load(sessions, outbox_id)
        assert status == "dead"
        assert row.last_error == "push: HTTP 400"

    run_committed(scenario)


def test_two_senders_at_once_send_only_once(line_user_id):
    async def scenario(sessions):
        outbox_id = await make_outbox(sessions, line_user_id, critical=False)
        line = FakeLine(reply=[200, 200], delay=0.05)
        t = now()

        results = await asyncio.gather(
            outbox.deliver(sessions, outbox_id, line, t),
            outbox.deliver(sessions, outbox_id, line, t),
        )

        assert sorted(results) == ["sent", "skipped"]
        assert len(line.calls) == 1

    run_committed(scenario)


def test_queue_reply_twice_saves_one_row(line_user_id):
    async def scenario(sessions):
        event_id = await add_text_event(sessions, line_user_id, "hi")
        for _ in range(2):
            async with sessions.begin() as session:
                user = await get_or_create_user(session, line_user_id)
                event = await session.get(InboundEvent, event_id)
                await outbox.queue_reply(
                    session, user, event, [outbox.text_message("ok")]
                )

        async with sessions() as session:
            rows = await session.scalars(
                select(OutboundMessage).where(OutboundMessage.user_id == user.id)
            )
            assert len(list(rows)) == 1

    run_committed(scenario)


def test_acknowledge_handler_queues_a_reply(line_user_id):
    async def scenario(sessions):
        await add_text_event(sessions, line_user_id, "สวัสดี")

        await process_user_inbox(sessions, line_user_id, "w1", handler=acknowledge)

        async with sessions() as session:
            row = await session.scalar(
                select(OutboundMessage)
                .join(User, User.id == OutboundMessage.user_id)
                .where(User.line_user_id == line_user_id)
            )
        assert row.status == "pending"
        assert row.messages == [{"type": "text", "text": "ได้รับข้อความแล้ว: สวัสดี"}]

    run_committed(scenario)