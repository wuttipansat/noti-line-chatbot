"""Daily digest: ONE morning message per user with today's and overdue tasks.

Why only one: every message the bot starts itself is a PUSH, and pushes count
toward the LINE Official Account's monthly message quota. One summary a day
(and none on days with nothing to do) keeps the cost predictable.

The outbox delivers it: no reply token + critical -> Push, with retries.
"""

import logging
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import replies
from app.db.models import OutboundMessage, User
from app.services import task_service as tasks
from app.services.dates import local_now, local_today

logger = logging.getLogger(__name__)


def digest_due(local: datetime, at: time, last_sent_on: date | None) -> bool:
    """Is it time for this user's digest? `local` = now in the user's timezone."""
    return local.time() >= at and last_sent_on != local.date()


async def users_due(session: AsyncSession, now: datetime, at: time) -> list[int]:
    """Active users whose digest time has come today and who did not get it yet."""
    users = await session.scalars(select(User).where(User.is_active.is_(True)))
    return [
        user.id
        for user in users
        if digest_due(local_now(now, user.timezone), at, user.last_digest_on)
    ]


async def queue_digest(session: AsyncSession, user_id: int, now: datetime) -> bool:
    """Put today's digest for one user into the outbox. True = a message was queued.

    Call inside a transaction. Safe to call twice: the outbox key
    "daily:{user_id}:{local date}" allows one digest per user per day (AC-23).
    """
    user = await session.get(User, user_id)
    today = local_today(now, user.timezone)
    if user.last_digest_on == today:
        return False
    user.last_digest_on = today

    view = await tasks.today_view(session, user.id, now)
    if not view.overdue and not view.today:
        return False

    message = replies.today_reply(view, user.timezone)
    message["text"] = "☀️ อรุณสวัสดิ์ครับ\n" + message["text"]
    queued_id = await session.scalar(
        insert(OutboundMessage)
        .values(
            user_id=user.id,
            idempotency_key=f"daily:{user.id}:{today.isoformat()}",
            critical=True,
            messages=[message],
        )
        .on_conflict_do_nothing(index_elements=[OutboundMessage.idempotency_key])
        .returning(OutboundMessage.id)
    )
    return queued_id is not None
