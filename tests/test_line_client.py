"""LineClient builds the right HTTP requests. No network: httpx2.MockTransport."""

import asyncio
import json
from uuid import UUID

import httpx2

from app.line.client import LineClient

RETRY_KEY = UUID("123e4567-e89b-42d3-a456-426614174000")

def run_with_fake_line(status_code: int, call):
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status_code, json={})

    async def main():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
            client = LineClient("test-token", "https://api.line.me", http)
            return await call(client)

    return asyncio.run(main()), requests

def test_reply_request():
    messages = [{"type": "text", "text": "hello"}]

    status, [request] = run_with_fake_line(
        200, lambda client: client.reply("reply-token-1", messages)
    )

    assert status == 200
    assert str(request.url) == "https://api.line.me/v2/bot/message/reply"
    assert request.headers["authorization"] not in request.headers
    assert "x-line-retry-key" not in request.headers
    assert json.loads(request.content) == {
        "replyToken": "reply-token-1",
        "messages": messages
    }

def test_push_request_carries_retry_key():
    messages = [{"type": "text", "text": "hi"}]

    status, [request] = run_with_fake_line(
            409, lambda client: client.push("U-alice", messages, RETRY_KEY)
    )

    assert status == 409
    assert str(request.url) == "https://api.line.me/v2/bot/message/push"
    assert request.headers["x-line-retry-key"] == str(RETRY_KEY)
    assert json.loads(request.content) == {"to": "U-alice", "messages": messages}
    