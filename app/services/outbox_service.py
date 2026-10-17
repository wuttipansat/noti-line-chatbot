"""Outbox: save outgoing messages first, deliver later.

Delivery rules:
1. Reply (free) if reply token is exist, never tried it, and are within 50 s.
2. Push (monthly quota) only for critical messages.
"""

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx2
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import InboundEvent, OutboundMessage, User
from app.line.client import LineClient

logger = logging.getLogger(__name__)

REPLY_WINDOW = timedelta(seconds=50)
SENDING_TIMEOUT = timedelta(minutes=2)
RETRY_KEY_LIFETIME = timedelta(hours=24)
MAX_MESSAGES = 5
MAX_TEXT_LENGTH = 5000


def text_message(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text[:MAX_TEXT_LENGTH]}


async def queue_reply(
    session: AsyncSession,
    user: User,
    event: InboundEvent,
    messages: list[dict[str, Any]],
    *,
    critical: bool = False,
) -> None:
    """Save the answer to this event."""
    if not 1 <= len(messages) <= MAX_MESSAGES:
        raise ValueError(f"1 to {MAX_MESSAGES} messages per send")
    await session.execute(
        pg_insert(OutboundMessage)
        .values(
            user_id=user.id,
            idempotency_key=f"reply:{event.id}",
            critical=critical,
            reply_token=event.reply_token,
            reply_deadline=event.event_ts + REPLY_WINDOW,
            messages=messages,
        )
        .on_conflict_do_nothing(index_elements=[OutboundMessage.idempotency_key])
    )


def _is_due(now: datetime):
    """New rows are always due. retries wait for next_attempt_at."""
    return or_(OutboundMessage.attempts == 0, OutboundMessage.next_attempt_at <= now)


async def due_outbox_ids(
    session: AsyncSession, now: datetime, user_id: int | None = None
) -> list[int]:
    query = select(OutboundMessage.id).where(
        OutboundMessage.status.in_(["pending", "sending"]),
        _is_due(now),
    )
    if user_id is not None:
        query = query.where(OutboundMessage.user_id == user_id)
    result = await session.scalars(query.order_by(OutboundMessage.id).limit(100))
    return list(result)


async def _claim(session: AsyncSession, outbox_id: int, now: datetime) -> bool:
    """Only one sender may work on a row at a time."""
    result = await session.execute(
        update(OutboundMessage)
        .where(
            OutboundMessage.id == outbox_id,
            OutboundMessage.status.in_(["pending", "sending"]),
            _is_due(now),
        )
        .values(
            status="sending",
            attempts=OutboundMessage.attempts + 1,
            next_attempt_at=now + SENDING_TIMEOUT,
        )
        .returning(OutboundMessage.id)
    )
    return result.scalar_one_or_none() is not None


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(5 * 3 ** (attempts - 1), 3600))  # 5, 15, 45 s...


async def deliver(
    sessions: async_sessionmaker, outbox_id: int, line: LineClient, now: datetime
) -> str:
    """Try to send one outbox row. Returns its new status."""
    async with sessions.begin() as session:
        if not await _claim(session, outbox_id, now):
            return "skipped"

    async with sessions() as session:
        msg = await session.get(OutboundMessage, outbox_id)
        to = await session.scalar(
            select(User.line_user_id).where(User.id == msg.user_id)
        )

    status, channel, error = await _send(msg, to, line, now)

    async with sessions.begin() as session:
        msg = await session.get(OutboundMessage, outbox_id)
        msg.status = status
        msg.channel_used = channel or msg.channel_used
        msg.last_error = error
        if status == "sent":
            msg.sent_at = now
        elif status == "pending":
            msg.next_attempt_at = now + _backoff(msg.attempts)
    logger.info("outbox %s -> %s via %s (%s)", outbox_id, status, channel, error)
    return status


async def _send(
    msg: OutboundMessage, to: str, line: LineClient, now: datetime
) -> tuple[str, str | None, str | None]:
    """The delivery rules. Returns (status, channel_used, error)."""
    reply_never_tried = msg.channel_used is None
    if msg.reply_token and reply_never_tried and now < msg.reply_deadline:
        try:
            code = await line.reply(msg.reply_token, msg.messages)
        except httpx2.TransportError as exc:
            code, error = None, f"reply: {type(exc).__name__}"
        else:
            error = f"reply: HTTP {code}"
        if code == 200:
            return "sent", "reply", None
        if not msg.critical:
            return (
                ("dead" if code == 400 else "unknown"),
                "reply",
                error,
            )  # 400 = token expired | No answer / 5xx = maybe delivered.
    elif not msg.critical:
        return "dead", None, "reply not possible and message is not critical"

    if now - msg.created_at > RETRY_KEY_LIFETIME:
        return "dead", "push", "retry key expired"
    try:
        code = await line.push(to, msg.messages, msg.retry_key)
    except httpx2.TransportError as exc:
        return "pending", "push", f"push: {type(exc).__name__}"
    if code in (200, 409):  # 409 = LINE already accepted this retry key
        return "sent", "push", None
    if code == 429 or code >= 500:
        return "pending", "push", f"push: HTTP {code}"
    return "dead", "push", f"push: HTTP {code}"
