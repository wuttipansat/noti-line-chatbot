"""Process each user's inbox in order, one worker per user at a time.

The "lease" is a lock with an expiry time, stored on the users row:
- A worker may process a user's events only while it holds the lease.
- If the worker crashes, the lease simply expires and another worker takes over.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import InboundEvent, User
from app.services.task_service import get_or_create_user

logger = logging.getLogger(__name__)

LEASE_SECONDS = 90
STALE_AFTER = timedelta(seconds=60)

Handler = Callable[[AsyncSession, User, InboundEvent], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(UTC)


async def acquire_lease(
    session: AsyncSession, user_id: int, owner: str, now: datetime
) -> bool:
    result = await session.execute(
        update(User)
        .where(
            User.id == user_id,
            or_(User.inbox_lease_until.is_(None), User.inbox_lease_until < now),
        )
        .values(
            inbox_lease_owner=owner,
            inbox_lease_until=now + timedelta(seconds=LEASE_SECONDS),
        )
        .returning(User.id)
    )

    return result.scalar_one_or_none() is not None


async def release_lease(session: AsyncSession, user_id: int, owner: str) -> None:
    await session.execute(
        update(User)
        .where(User.id == user_id, User.inbox_lease_owner == owner)
        .values(inbox_lease_owner=None, inbox_lease_until=None)
    )


async def next_pending_event(
    session: AsyncSession, line_user_id: str
) -> InboundEvent | None:
    return await session.scalar(
        select(InboundEvent)
        .where(
            InboundEvent.line_user_id == line_user_id,
            InboundEvent.status == "received",
        )
        .order_by(InboundEvent.event_ts, InboundEvent.id)
        .limit(1)
    )


async def log_only(session: AsyncSession, user: User, event: InboundEvent) -> None:
    text = (event.payload.get("message") or {}).get("text") or ""
    logger.info("user %s sent %s (%d chars)", user.id, event.event_type, len(text))


async def _process_one(
    sessions: async_sessionmaker, user: User, event_id: int, handler: Handler
) -> None:
    try:
        async with sessions.begin() as session:
            event = await session.get(InboundEvent, event_id)
            await handler(session, user, event)
            event.status = "done"
            event.attempts += 1

    except Exception as exc:
        logger.exception("event %s failed", event_id)
        async with sessions.begin() as session:
            event = await session.get(InboundEvent, event_id)
            event.status = "failed"
            event.attempts += 1
            event.error = type(exc).__name__


async def process_user_inbox(
    sessions: async_sessionmaker,
    line_user_id: str,
    owner: str,
    handler: Handler = log_only,
    now: Callable[[], datetime] = _now,
) -> int:
    handled = 0
    async with sessions.begin() as session:
        user = await get_or_create_user(session, line_user_id)

    while True:
        async with sessions.begin() as session:
            if not await acquire_lease(session, user.id, owner, now()):
                return handled

        try:
            while True:
                async with sessions.begin() as session:
                    event = await next_pending_event(session, line_user_id)
                if event is None:
                    break
                await _process_one(sessions, user, event.id, handler)
                handled += 1
        finally:
            async with sessions.begin() as session:
                await release_lease(session, user.id, owner)

        async with sessions.begin() as session:
            if await next_pending_event(session, line_user_id) is None:
                return handled


async def stale_line_user_ids(session: AsyncSession, now: datetime) -> list[str]:
    result = await session.scalars(
        select(InboundEvent.line_user_id)
        .where(
            InboundEvent.status == "received",
            InboundEvent.received_at < now - STALE_AFTER,
        )
        .distinct()
    )
    return list(result)
