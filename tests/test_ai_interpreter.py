"""Validation of model output (AC-14). No database, no network: a fake provider."""

import asyncio
import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx2
import pytest

from app.ai.interpreter import (
    MAX_COMMANDS,
    Rejected,
    interpret,
    interpret_explained,
    interpret_plan,
    validate,
    validate_plan,
)
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
        as_json(action="add", title="x", due_date="2026-10-16", due_time="25:00"),
        as_json(action="add", title="x", priority=9),
        as_json(action="add", title="x" * 201),
        as_json(action="done", task_no=2),  # not an open task of this user
        as_json(action="done"),
        as_json(action="add", title="x", time_options=["08:00"]),  # nothing to choose
        as_json(action="add", title="x", time_options=["8 โมง", "14:00"]),
        as_json(
            action="add", title="x", due_time="08:00", time_options=["08:00", "14:00"]
        ),
        as_json(action="today", time_options=["08:00", "14:00"]),
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


def test_ambiguous_time_becomes_a_question():
    raw = as_json(action="add", title="ประชุมทีม", time_options=["14:00", "08:00"])

    command = validate(raw, CONTEXT)

    assert command == Command(
        "ask_time",
        title="ประชุมทีม",
        due_date=date(2026, 10, 15),  # no day given = today
        time_options=("08:00", "14:00"),
    )


def test_time_without_a_day_is_today_if_still_ahead():
    context = ExtractContext(date(2026, 10, 15), "Asia/Bangkok", [], now=time(9, 0))

    command = validate(
        as_json(action="add", title="โทรหาลูกค้า", due_time="15:00"), context
    )

    assert command.due_at == datetime(2026, 10, 15, 15, 0, tzinfo=BKK)


def test_time_without_a_day_that_already_passed_is_tomorrow():
    context = ExtractContext(date(2026, 10, 15), "Asia/Bangkok", [], now=time(9, 0))

    command = validate(as_json(action="add", title="วิ่ง", due_time="05:00"), context)

    assert command.due_at == datetime(2026, 10, 16, 5, 0, tzinfo=BKK)


def test_short_times_like_9_00_are_accepted():
    raw = as_json(action="add", title="ประชุม", due_date="2026-10-16", due_time="9:00")

    assert validate(raw, CONTEXT).due_at == datetime(2026, 10, 16, 9, 0, tzinfo=BKK)


def test_explained_interpret_says_why():
    rejected = FakeProvider(as_json(action="done", task_no=99))
    crashed = FakeProvider(TimeoutError("slow"))

    assert asyncio.run(interpret_explained("x", CONTEXT, rejected)) == (
        None,
        "rejected: task #99 is not one of this user's open tasks",
    )
    assert asyncio.run(interpret_explained("x", CONTEXT, crashed)) == (
        None,
        "provider failed: TimeoutError",
    )


def test_busy_provider_logs_one_line_not_a_traceback(caplog):
    request = httpx2.Request("POST", "https://ai.example/v1")
    busy = httpx2.HTTPStatusError(
        "busy", request=request, response=httpx2.Response(503, request=request)
    )

    result = asyncio.run(interpret_explained("x", CONTEXT, FakeProvider(busy)))

    assert result == (None, "provider failed: HTTP 503")
    [record] = [r for r in caplog.records if r.name == "app.ai.interpreter"]
    assert record.levelname == "WARNING"
    assert record.exc_info is None


def many(*items: dict) -> str:
    return json.dumps({"commands": list(items)}, ensure_ascii=False)


def add(title, due_date=None, **more) -> dict:
    return {"action": "add", "title": title, "due_date": due_date, **more}


def test_several_tasks_become_several_commands_in_order():
    raw = many(
        add("ส่งรายงาน", "2026-10-16"),
        add("ประชุมทีม", "2026-10-17", due_time="14:00"),
    )

    plan = validate_plan(raw, CONTEXT)

    assert [c.title for c in plan.commands] == ["ส่งรายงาน", "ประชุมทีม"]
    assert plan.commands[0].due_date == date(2026, 10, 16)
    assert plan.commands[1].due_at == datetime(2026, 10, 17, 14, 0, tzinfo=BKK)
    assert plan.skipped == ()


def test_done_and_add_can_be_mixed():
    raw = many({"action": "done", "task_no": 3}, add("ประชุม", "2026-10-16"))

    plan = validate_plan(raw, CONTEXT)

    assert [c.action for c in plan.commands] == ["done", "add"]


def test_a_bad_item_is_skipped_and_reported_the_good_ones_stay():
    raw = many(
        add("ส่งรายงาน", "2026-10-16"),
        add("วันที่ไม่มีจริง", "2026-02-30"),
        {"action": "done", "task_no": 99},  # not this user's task
        {"action": "add", "title": "ok", "surprise": 1},  # a field we never allowed
    )

    plan = validate_plan(raw, CONTEXT)

    assert [c.title for c in plan.commands] == ["ส่งรายงาน"]
    assert [x.label for x in plan.skipped] == ["วันที่ไม่มีจริง", None, "ok"]


def test_only_tasks_can_be_combined():
    raw = many(add("ส่งรายงาน", "2026-10-16"), {"action": "all"}, {"action": "unknown"})

    plan = validate_plan(raw, CONTEXT)

    assert len(plan.commands) == 1
    assert len(plan.skipped) == 2


def test_more_than_the_limit_is_kept_in_part_and_the_rest_is_reported():
    raw = many(*[add(f"งาน {n}", "2026-10-16") for n in range(MAX_COMMANDS + 2)])

    plan = validate_plan(raw, CONTEXT)

    assert len(plan.commands) == MAX_COMMANDS
    assert [x.label for x in plan.skipped] == [
        f"งาน {n}" for n in range(MAX_COMMANDS, MAX_COMMANDS + 2)
    ]
    assert all(x.too_many for x in plan.skipped)


def test_an_absurd_number_of_items_rejects_the_whole_answer():
    raw = many(*[add(f"งาน {n}") for n in range(50)])

    with pytest.raises(Rejected, match="50 commands"):
        validate_plan(raw, CONTEXT)


def test_the_same_task_twice_is_kept_once():
    raw = many(add("ส่งรายงาน", "2026-10-16"), add("ส่งรายงาน", "2026-10-16"))

    assert len(validate_plan(raw, CONTEXT).commands) == 1


def test_only_one_time_question_per_message():
    ask = {"time_options": ["06:00", "18:00"]}
    raw = many(add("ประชุมทีม", **ask), add("วิ่ง", **ask), add("ส่งงาน"))

    plan = validate_plan(raw, CONTEXT)

    assert [c.action for c in plan.commands] == ["ask_time", "add"]
    assert [x.label for x in plan.skipped] == ["วิ่ง"]


def test_a_single_bare_command_without_the_wrapper_still_works():
    raw = as_json(action="add", title="ส่งรายงาน", due_date="2026-10-16")

    assert [c.title for c in validate_plan(raw, CONTEXT).commands] == ["ส่งรายงาน"]


@pytest.mark.parametrize(
    "raw", ['{"commands": []}', "[]", "not json", '{"commands": 5}']
)
def test_empty_or_broken_answers_are_rejected(raw):
    with pytest.raises(Rejected):
        validate(raw, CONTEXT)


def test_validate_stays_strict_for_a_single_command():
    with pytest.raises(Rejected, match="several"):
        validate(many(add("ก", "2026-10-16"), add("ข", "2026-10-16")), CONTEXT)


def test_interpret_plan_explains_partial_and_failed_answers():
    partial = many(add("ก", "2026-10-16"), add("ข", "2026-02-30"))
    plan, why = asyncio.run(interpret_plan("x", CONTEXT, FakeProvider(partial)))
    assert (len(plan.commands), len(plan.skipped), why) == (1, 1, "ok")

    plan, why = asyncio.run(
        interpret_plan("x", CONTEXT, FakeProvider(many(add("ข", "2026-02-30"))))
    )
    assert plan.commands == ()
    assert why.startswith("rejected: not a real date")
