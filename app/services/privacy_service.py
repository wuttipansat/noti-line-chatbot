"""Personal data: delete everything a user has, on request (AC-29).

What is deleted: tasks, queued messages, the pending question, and every
saved incoming message. The current message is kept (the inbox still has to
mark it done) but its text is wiped. The user row stays, reset to a fresh
start: the confirmation reply needs it, and keeping the AI counter stops a
"delete then start over" trick from resetting today's AI budget.
"""

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConversationState, InboundEvent, OutboundMessage, Task, User


async def forget_user(session: AsyncSession, user: User, event: InboundEvent) -> None:
    """Call inside the inbox transaction: all of it commits together, or none."""
    await session.execute(delete(Task).where(Task.user_id == user.id))
    await session.execute(
        delete(OutboundMessage).where(OutboundMessage.user_id == user.id)
    )
    await session.execute(
        delete(ConversationState).where(ConversationState.user_id == user.id)
    )
    await session.execute(
        delete(InboundEvent).where(
            InboundEvent.line_user_id == user.line_user_id,
            InboundEvent.id != event.id,
        )
    )
    event.payload = {"type": event.event_type}  # keep the row, drop the text
    await session.execute(
        update(User)
        .where(User.id == user.id)
        .values(next_task_no=1, last_digest_on=None)
    )


async def set_active(session: AsyncSession, user: User, active: bool) -> None:
    """unfollow (blocked) -> inactive: no daily digest pushes to people who left."""
    await session.execute(
        update(User).where(User.id == user.id).values(is_active=active)
    )
