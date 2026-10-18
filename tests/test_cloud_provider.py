"""CloudProvider builds the right request. No network: httpx2.MockTransport."""

import asyncio
import json
from datetime import date

import httpx2
import pytest

from app.ai.cloud import CloudProvider
from app.ai.interpreter import interpret
from app.ai.prompt import OUTPUT_SCHEMA
from app.ai.provider import ExtractContext, OpenTask
from app.ai.schema import AiIntent
from app.bot.commands import Command

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
CONTEXT = ExtractContext(
    today=date(2026, 10, 18),
    timezone="Asia/Bangkok",
    open_tasks=[OpenTask(3, "ส่งรายงาน")],
)


def fake_cloud(answer: dict | None = None, status: int = 200):
    """A pretend Chat Completions server. Returns (transport, received requests)."""
    received: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        received.append(request)
        content = json.dumps(answer or {"action": "unknown"})
        return httpx2.Response(
            status,
            json={
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 400, "completion_tokens": 40},
            },
        )

    return httpx2.MockTransport(handler), received


def provider(transport, json_mode="json_schema") -> CloudProvider:
    return CloudProvider(
        BASE_URL, "test-key", "gemini-3.8-flash", 5, json_mode, transport=transport
    )


def test_request_has_auth_model_schema_and_context():
    transport, received = fake_cloud()

    asyncio.run(provider(transport).extract("หวัดดี", CONTEXT))

    [request] = received
    body = json.loads(request.content)
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert body["model"] == "gemini-3.8-flash"
    assert body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == OUTPUT_SCHEMA
    user = body["messages"][1]["content"]
    assert "2026-10-18 (Sunday)" in user
    assert "#3 ส่งรายงาน" in user
    assert user.endswith("Message:\nหวัดดี")


def test_json_object_mode_for_providers_without_schema_support():
    transport, received = fake_cloud()

    asyncio.run(provider(transport, "json_object").extract("หวัดดี", CONTEXT))

    assert json.loads(received[0].content)["response_format"] == {"type": "json_object"}


def test_answer_goes_through_the_same_validation():
    answer = {
        "action": "done",
        "title": None,
        "task_no": 3,
        "due_date": None,
        "due_time": None,
        "priority": 2,
        "estimated_minutes": None,
    }
    transport, _ = fake_cloud(answer)

    command = asyncio.run(interpret("ส่งรายงานเสร็จแล้ว", CONTEXT, provider(transport)))

    assert command == Command("done", task_no=3)


@pytest.mark.parametrize("status", [401, 429, 500])
def test_http_errors_are_raised_so_fallback_can_take_over(status):
    transport, _ = fake_cloud(status=status)

    with pytest.raises(httpx2.HTTPStatusError):
        asyncio.run(provider(transport).extract("...", CONTEXT))


def test_schema_has_exactly_the_fields_our_validator_knows():
    assert set(OUTPUT_SCHEMA["properties"]) == set(AiIntent.model_fields)
    assert set(OUTPUT_SCHEMA["required"]) == set(AiIntent.model_fields)
