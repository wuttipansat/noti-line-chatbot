"""Turn a LINE webhook body into rows for the inbound_events table"""

from datetime import UTC, datetime
from typing import Any

def to_inbox_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep only events we can process: they need an id and a 1:1 chat user."""
    rows = []
    for event in body.get("events", []):
        event_id = event.get("webhookEventId")
        source = event.get("source") or {}
        user_id = source.get("userId")
        if not event_id or source.get("type") != "user" or not user_id:
            continue
        rows.append(
            {
                "webhook_event_id": event_id,
                "line_user_id": user_id,
                "event_type": event.get("type", "unknown"),
                "event_ts": datetime.fromtimestamp(event["timestamp"] / 1000, UTC),
                "is_redelivery": bool(
                    (event.get("deliveryContext") or {}).get("isRedelivery")
                ),
                "reply_token": event.get("replyToken"),
                "payload": event,
            }
        )

    return rows