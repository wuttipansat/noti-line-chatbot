"""POST /webhook/line - verify, save to inbox, reply 200 fast."""

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import InboundEvent
from app.line.events import to_inbox_rows
from app.line.signature import is_valid_signature

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/webhook/line")
async def line_webhook(request: Request) -> dict[str, int]:
    settings = request.app.state.settings
    if settings.line_channel_secret is None:
        raise HTTPException(status_code=503, detail="LINE channel secret not set")

    body = await request.body()
    signature = request.headers.get("x-line-signature")
    secret = settings.line_channel_secret.get_secret_value()
    if not is_valid_signature(secret, body, signature):
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        rows = to_inbox_rows(json.loads(body))
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="malformed body") from exc

    if rows:
        async with request.app.state.sessionmaker.begin() as session:
            await session.execute(
                pg_insert(InboundEvent)
                .values(rows)
                .on_conflict_do_nothing(index_elements=[InboundEvent.webhook_event_id])
            )

    for line_user_id in {row["line_user_id"] for row in rows}:
        try:
            request.app.state.enqueue_inbox(line_user_id)
        except Exception:
            logger.exception("could not enqueue inbox for %s", line_user_id)

    return {"accepted": len(rows)}


