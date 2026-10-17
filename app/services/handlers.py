"""What the bot does with each incoming event.

Current: only confirms it received a text message.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InboundEvent, User
from app.services.outbox_service import queue_reply, text_message


async def acknowledge(session: AsyncSession, user: User, event: InboundEvent) -> None:
    message = event.payload.get("message") or {}
    if event.event_type != "message" or message.get("type") != "text":
        return
    await queue_reply(
        session, user, event, [text_message(f"ได้รับข้อความแล้ว: {message['text']}")]
    )
