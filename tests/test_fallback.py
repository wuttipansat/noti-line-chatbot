"""When the real model fails, the mock answers (AC-15). No network."""

import asyncio
import json
from datetime import date

from app.ai.factory import build_provider
from app.ai.fallback import FallbackProvider
from app.ai.interpreter import interpret
from app.ai.mock import MockProvider
from app.ai.provider import ExtractContext
from app.bot.commands import Command
from app.config import Settings

CONTEXT = ExtractContext(
    today=date(2026, 10, 17), timezone="Asia/Bangkok", open_tasks=[]
)


class Broken:
    name = "broken"

    def __init__(self):
        self.calls = 0

    async def extract(self, text, context):
        self.calls += 1
        raise TimeoutError("model did not answer")


class Fixed:
    name = "fixed"

    def __init__(self, answer: dict):
        self.answer = answer
        self.calls = 0

    async def extract(self, text, context):
        self.calls += 1
        return json.dumps(self.answer)


def test_backup_answers_when_primary_times_out():
    primary = Broken()
    provider = FallbackProvider(primary, MockProvider())

    command = asyncio.run(interpret("วันนี้ 17:00 โทรหาลูกค้า", CONTEXT, provider))

    assert primary.calls == 1
    assert command.action == "add"
    assert command.title == "โทรหาลูกค้า"


def test_backup_is_not_used_when_primary_works():
    backup = Fixed({"action": "help"})
    provider = FallbackProvider(Fixed({"action": "today"}), backup)

    assert asyncio.run(interpret("วันนี้มีอะไร", CONTEXT, provider)) == Command("today")
    assert backup.calls == 0


def test_default_is_mock_only():
    provider = build_provider(Settings(_env_file=None))

    assert isinstance(provider, MockProvider)


def test_cloud_without_key_falls_back_to_mock_only():
    provider = build_provider(Settings(_env_file=None, llm_provider="cloud"))

    assert isinstance(provider, MockProvider)


def test_cloud_with_key_comes_with_mock_backup():
    settings = Settings(_env_file=None, llm_provider="cloud", llm_api_key="test-key")

    provider = build_provider(settings)

    assert isinstance(provider, FallbackProvider)
    assert provider.name == "cloud+mock"
