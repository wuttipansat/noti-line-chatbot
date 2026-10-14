"""POST /webhook/line against real PostgreSQL"""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db.models import InboundEvent
from app.line.signature import compute_signature
from app.main import create_app
from tests.helpers import database_url

SECRET = "test-channel-secret"

def make_event(event_id: str, redelivery: bool = False) -> dict:
    return {
        "type": "message",
        "webhookEventId": event_id,
        "timestamp": 1791970200000,
        "source": {"type": "user", "userId": "U-webhook-test"},
        "replyToken": "reply-token",
        "deliveryContext": {"isRedelivery": redelivery},
        "mode": "active",
        "message": {"id": "1", "type": "text", "text": "งานวันนี้"},
    }

def to_bytes(body: dict) -> bytes:
    return json.dumps(body, ensure_ascii=False).encode()

def post(client: TestClient, body: bytes, signature: str | None):
    headers = {"content-type": "application/json"}
    if signature is not None:
        headers["x-line-signature"] = signature
    return client.post("/webhook/line", content=body, headers=headers)

async def _count_rows(event_id: str) -> int:
    engine = create_async_engine(database_url())
    try:
        async with engine.connect() as conn:
            return await conn.scalar(
                select(func.count())
                .select_from(InboundEvent)
                .where(InboundEvent.webhook_event_id == event_id)
            )
    finally:
        await engine.dispose()

async def _delete_rows(event_id: str) -> None:
    engine = create_async_engine(database_url())
    try:
        async with engine.begin() as conn:
            await conn.execute(
                delete(InboundEvent).where(InboundEvent.webhook_event_id == event_id)
            )
    finally:
        await engine.dispose()

def count_rows(event_id: str) -> int:
    return asyncio.run(_count_rows(event_id))

@pytest.fixture
def event_id():
    new_id = f"test-{uuid4()}"
    yield new_id
    asyncio.run(_delete_rows(new_id))

@pytest.fixture
def enqueued():
    return []

@pytest.fixture
def client(enqueued):
    app = create_app(Settings(_env_file=None, line_channel_secret=SECRET))
    app.state.enqueue_inbox = enqueued.append
    with TestClient(app) as test_client:
        yield test_client

def test_webhook_saves_valid_event(client, event_id):
    body = to_bytes({"events": [make_event(event_id)]})

    response = post(client, body, compute_signature(SECRET, body))

    assert response.status_code == 200
    assert response.json() == {"accepted": 1}
    assert count_rows(event_id) == 1

def test_webhook_rejects_bad_signature(client, event_id):
    body = to_bytes({"events": [make_event(event_id)]})
    good_signature = compute_signature(SECRET, body)
    tampered = body.replace(b"reply-token", b"reply-tokeN")

    assert post(client, tampered, good_signature).status_code == 401
    assert post(client, body, "not-a-signature").status_code == 401
    assert post(client, body, None).status_code == 401
    assert count_rows(event_id) == 0

def test_webhook_empty_events_verification(client):
    body = to_bytes({"destination": "U0", "events": []})

    response = post(client, body, compute_signature(SECRET, body))

    assert response.status_code == 200
    assert response.json() == {"accepted": 0}

def test_duplicate_webhook_is_saved_once(client, event_id):
    first = to_bytes({"events": [make_event(event_id)]})
    again = to_bytes({"events": [make_event(event_id, redelivery=True)]})

    for body in [first, first, again]:
        response = post(client, body, compute_signature(SECRET, body))
        assert response.status_code == 200

    assert count_rows(event_id) == 1

def test_webhook_rejects_malformed_json(client):
    body = b"{not json"

    response = post(client, body, compute_signature(SECRET, body))

    assert response.status_code == 400

def test_webhook_refuses_when_secret_missing(event_id):
    app = create_app(Settings(_env_file=None, line_channel_secret=""))
    body = to_bytes({"events": [make_event(event_id)]})

    with TestClient(app) as test_client:
        response = post(test_client, body, compute_signature(SECRET, body))

    assert response.status_code == 503
    assert count_rows(event_id) == 0

def test_webhook_wakes_worker_once_per_user(client, enqueued, event_id):
    second_id = f"{event_id}-2"
    body = to_bytes({"events": [make_event(event_id), make_event(second_id)]})

    try:
        response = post(client, body, compute_signature(SECRET, body))
    finally:
        asyncio.run(_delete_rows(second_id))

    assert response.status_code == 200
    assert enqueued == ["U-webhook-test"]

def test_webhook_still_saves_when_redis_is_down(event_id):
    def broken_enqueue(line_user_id: str) -> None:
        raise ConnectionError("redis is down")

    app = create_app(Settings(_env_file=None, line_channel_secret=SECRET))
    app.state.enqueue_inbox = broken_enqueue
    body = to_bytes({"events": [make_event(event_id)]})

    with TestClient(app) as test_client:
        response = post(test_client, body, compute_signature(SECRET, body))

    assert response.status_code == 200
    assert count_rows(event_id) == 1
