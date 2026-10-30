"""Questions the bot is waiting for an answer to (conversation_states).

One row per user at most (user_id is the primary key), so one user's pending
question can never be answered by another user (AC-12).
Lives in PostgreSQL, not Redis: the question is saved, answered and cleared in
the SAME transaction as the task change and the reply in the outbox.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.commands import Command
from app.db.models import ConversationState, InboundEvent

QUESTION_TTL = timedelta(minutes=10)


async def _save_state(
    session: AsyncSession, user_id: int, event: InboundEvent, kind: str, payload: dict
) -> None:
    """Remember a question. A newer question replaces an older one (upsert)."""
    values = {
        "user_id": user_id,
        "kind": kind,
        "payload": payload,
        "source_event_id": event.id,
        "expires_at": event.event_ts + QUESTION_TTL,
    }
    statement = insert(ConversationState).values(**values)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[ConversationState.user_id], set_=values
        )
    )


async def ask_time(
    session: AsyncSession, user_id: int, event: InboundEvent, command: Command
) -> None:
    """Remember the half-finished task until the user picks a time."""
    payload = {
        "title": command.title,
        "due_date": command.due_date.isoformat(),
        "priority": command.priority,
        "estimated_minutes": command.estimated_minutes,
        "options": list(command.time_options),
    }
    await _save_state(session, user_id, event, "clarify_time", payload)


async def ask_forget_confirmation(
    session: AsyncSession, user_id: int, event: InboundEvent
) -> None:
    """Deleting everything cannot be undone: wait for an explicit confirmation."""
    await _save_state(session, user_id, event, "confirm_forget", {})


async def pop_state(session: AsyncSession, user_id: int) -> ConversationState | None:
    """Take the pending question out (delete + return it): it is answered only once."""
    return await session.scalar(
        delete(ConversationState)
        .where(ConversationState.user_id == user_id)
        .returning(ConversationState)
    )


def answer_time(state: ConversationState, choice: str, timezone: str) -> Command:
    """The user picked one of the offered times -> the finished add command."""
    payload = state.payload
    due_at = datetime.combine(
        date.fromisoformat(payload["due_date"]),
        time.fromisoformat(choice),
        ZoneInfo(timezone),
    )
    return Command(
        "add",
        title=payload["title"],
        due_at=due_at,
        priority=payload["priority"],
        estimated_minutes=payload["estimated_minutes"],
    )
