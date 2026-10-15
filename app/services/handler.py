"""What the bot does with each incoming event.

Current: only confirms it received a text message.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InboundEvent, User
from app.services.outbox_service import queue_reply, text_message