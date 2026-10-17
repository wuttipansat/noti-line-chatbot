"""Validation of model output (AC-14). No database, no network: a fake provider."""

import asyncio
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.ai.interpreter import Rejected, interpret, validate
from app.ai.provider import ExtractContext, OpenTask
from app.bot.commands import Command

BKK = ZoneInfo("Asia/Bangkok")
CONTEXT = ExtractContext(
    today=date(2026, 10, 15),
    timezone="Asia/Bangkok",
    open_tasks=[OpenTask(1, "อ่าน paper"), OpenTask(3, "ส่งรายงาน")],
)


class FakeProvider:
    """Returns whatever text we give it — or raises, to simulate an outage."""

    name = "fake"

    def __init__(self, answer: str | Exception):
        self.answer = answer
        self.calls: list[str] = []

    async def extract(self, text, context):
        self.calls.append(text)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def as_json(**fields) -> str:
    return json.dumps(fields, ensure_ascii=False)


def test_add_with_date_and_time_becomes_local_due_at():
    raw = as_json(
        action="add", title="ส่งรายงาน", due_date="2026-10-16", due_time="14:00"
    )

    command = validate(raw, CONTEXT)

    assert command == Command(
        "add", title="ส่งรายงาน", due_at=datetime(2026, 10, 16, 14, 0, tzinfo=BKK)
    )


def test_add_with_date_only():
    command = validate(
        as_json(action="add", title="จ่ายค่าไฟ", due_date="2026-10-20"), CONTEXT
    )

    assert command.due_date == date(2026, 10, 20)
    assert command.due_at is None


def test_done_on_own_task():
    assert validate(as_json(action="done", task_no=3), CONTEXT) == Command(
        "done", task_no=3
    )


def test_code_fences_around_json_are_tolerated():
    raw = '```json\n{"action": "today"}\n```'

    assert validate(raw, CONTEXT) == Command("today")


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "",
        as_json(action="delete_everything"),  # not in the allowlist
        as_json(action="add", title="x", sql="DROP TABLE tasks"),  # unknown field
        as_json(action="add"),  # no title
        as_json(action="add", title="   "),
        as_json(action="add", title="x", due_date="2026-02-30"),  # no such date
        as_json(action="add", title="x", due_date="15/10/2026"),  # wrong format
        as_json(action="add", title="x", due_date="2026-10-14"),  # in the past
        as_json(action="add", title="x", due_date="2030-01-01"),  # too far ahead
        as_json(action="add", title="x", due_time="14:00"),  # time without date
        as_json(action="add", title="x", due_date="2026-10-16", due_time="25:00"),
        as_json(action="add", title="x", priority=9),
        as_json(action="add", title="x" * 201),
        as_json(action="done", task_no=2),  # not an open task of this user
        as_json(action="done"),
    ],
)
def test_bad_answers_are_rejected(raw):
    with pytest.raises(Rejected):
        validate(raw, CONTEXT)


def test_interpret_returns_none_when_rejected():
    provider = FakeProvider(as_json(action="done", task_no=99))

    assert asyncio.run(interpret("#99 เสร็จ", CONTEXT, provider)) is None


def test_interpret_returns_none_when_provider_crashes():
    provider = FakeProvider(TimeoutError("model did not answer"))

    assert asyncio.run(interpret("พรุ่งนี้ส่งงาน", CONTEXT, provider)) is None
    assert provider.calls == ["พรุ่งนี้ส่งงาน"]
