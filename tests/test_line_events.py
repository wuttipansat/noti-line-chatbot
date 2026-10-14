"""Webhook body -> inbox rows. Pure function, no database needed."""

from datetime import UTC, datetime

from app.line.events import to_inbox_rows


def message_event(**overrides) -> dict:
    event = {
        "type": "message",
        "webhookEventId": "01HTEST000000000000000000A",
        "timestamp": 1791970200000,  # 2026-10-14 09:30:00 UTC
        "source": {"type": "user", "userId": "U-alice"},
        "replyToken": "reply-token-1",
        "deliveryContext": {"isRedelivery": False},
        "mode": "active",
        "message": {"id": "1", "type": "text", "text": "งานวันนี้"},
    }
    event.update(overrides)
    return event


def test_message_event_becomes_row():
    [row] = to_inbox_rows({"events": [message_event()]})

    assert row["webhook_event_id"] == "01HTEST000000000000000000A"
    assert row["line_user_id"] == "U-alice"
    assert row["event_type"] == "message"
    assert row["event_ts"] == datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    assert row["is_redelivery"] is False
    assert row["reply_token"] == "reply-token-1"
    assert row["payload"]["message"]["text"] == "งานวันนี้"


def test_redelivery_flag_is_kept():
    event = message_event(deliveryContext={"isRedelivery": True})

    [row] = to_inbox_rows({"events": [event]})

    assert row["is_redelivery"] is True


def test_empty_events_gives_no_rows():
    assert to_inbox_rows({"destination": "U0", "events": []}) == []


def test_skips_group_chats_and_events_without_id():
    group = message_event(source={"type": "group", "groupId": "C1", "userId": "U1"})
    no_id = message_event()
    del no_id["webhookEventId"]

    assert to_inbox_rows({"events": [group, no_id]}) == []