"""Per-user limits, so one user cannot use up what everyone shares.

1. Message rate limit (AC-28): at most N messages per window. The first message
   over the limit gets ONE warning; the rest are ignored (no reply, no AI).
2. Daily AI budget: at most N language-model calls per user per local day.
   Over budget -> the free rule-based MockProvider is used instead.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import InboundEvent, User
from app.services.dates import local_today


@dataclass(frozen=True)
class Limits:
    messages: int = 20
    window: timedelta = timedelta(minutes=10)
    ai_per_day: int = 10

    @classmethod
    def from_settings(cls, settings: Settings) -> "Limits":
        return cls(
            messages=settings.rate_limit_messages,
            window=timedelta(minutes=settings.rate_limit_window_minutes),
            ai_per_day=settings.ai_daily_limit_per_user,
        )


async def messages_in_window(
    session: AsyncSession, user: User, event: InboundEvent, window: timedelta
) -> int:
    """How many messages this user sent in the window ending at this one (included)."""
    return await session.scalar(
        select(func.count())
        .select_from(InboundEvent)
        .where(
            InboundEvent.line_user_id == user.line_user_id,
            InboundEvent.event_type == "message",
            InboundEvent.event_ts > event.event_ts - window,
            InboundEvent.event_ts <= event.event_ts,
        )
    )


async def take_ai_call(
    session: AsyncSession, user: User, now: datetime, per_day: int
) -> bool:
    """Use one AI call from today's budget. False = budget used up for today.

    One UPDATE does check + count together, in the database. (The `user` given to
    the handler was loaded in another session, so changing its attributes would
    not be saved.)
    """
    today = local_today(now, user.timezone)
    new_day = or_(User.ai_calls_on.is_(None), User.ai_calls_on != today)
    result = await session.execute(
        update(User)
        .where(User.id == user.id, or_(new_day, User.ai_calls_today < per_day))
        .values(
            ai_calls_on=today,
            ai_calls_today=case((new_day, 1), else_=User.ai_calls_today + 1),
        )
        .returning(User.id)
    )
    return result.scalar_one_or_none() is not None
