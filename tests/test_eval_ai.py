"""The AI scorecard (scripts/eval_ai.py) scores answers correctly. No network."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2

from app.ai.mock import MockProvider
from app.bot.commands import Command
from scripts.eval_ai import CASES, Case, check, report, run

BKK = ZoneInfo("Asia/Bangkok")


def test_a_correct_answer_has_no_problems():
    case = Case("พรุ่งนี้บ่ายสองส่งรายงาน", "add", "2026-10-29", "14:00", "ส่งรายงาน")
    command = Command(
        "add", title="ส่งรายงาน", due_at=datetime(2026, 10, 29, 14, 0, tzinfo=BKK)
    )

    assert check(case, command) == []


def test_wrong_day_and_time_are_both_reported():
    case = Case("พรุ่งนี้บ่ายสองส่งรายงาน", "add", "2026-10-29", "14:00")
    command = Command(
        "add", title="ส่งรายงาน", due_at=datetime(2026, 10, 30, 2, 0, tzinfo=BKK)
    )

    assert check(case, command) == [
        "day 2026-10-30 (expected 2026-10-29)",
        "time 02:00 (expected 14:00)",
    ]


def test_no_answer_counts_as_unknown():
    assert check(Case("สวัสดีครับ", "unknown"), None) == []
    assert check(Case("ส่งรายงานเสร็จแล้ว", "done", task_no=3), None) == [
        "action unknown (expected done)"
    ]


def test_whole_run_with_the_mock():
    results = asyncio.run(run(MockProvider()))

    assert len(results) == len(CASES)
    assert report(results).splitlines()[-1].startswith("ผ่าน ")


class BusyThenFine:
    """Answers 503 a few times, then a valid "unknown"."""

    name = "busy"

    def __init__(self, busy_calls: int):
        self.busy_calls = busy_calls
        self.calls = 0

    async def extract(self, text, context):
        self.calls += 1
        if self.calls <= self.busy_calls:
            request = httpx2.Request("POST", "https://ai.example/v1")
            response = httpx2.Response(503, request=request)
            raise httpx2.HTTPStatusError("busy", request=request, response=response)
        return '{"action": "unknown"}'


def test_busy_provider_is_asked_again(monkeypatch):
    monkeypatch.setattr("scripts.eval_ai.CASES", [Case("สวัสดีครับ", "unknown")])
    provider = BusyThenFine(busy_calls=2)

    [result] = asyncio.run(run(provider, waits=(0, 0)))

    assert provider.calls == 3
    assert result.ok is True


def test_never_answered_is_skipped_not_wrong(monkeypatch):
    cases = [Case("สวัสดีครับ", "unknown"), Case("หวัดดี", "unknown")]
    monkeypatch.setattr("scripts.eval_ai.CASES", cases)

    results = asyncio.run(run(BusyThenFine(busy_calls=99), waits=(0,)))
    text = report(results)

    assert [r.ok for r in results] == [None, None]
    assert "skipped (provider failed: HTTP 503)" in text
    assert "ข้าม 2 ข้อ" in text
    assert "ผ่าน" not in text


def test_also_ok_accepts_another_good_action():
    case = Case("สวัสดีครับ", "unknown", also_ok=("help",))

    assert check(case, Command("help")) == []
    assert check(case, Command("today")) == ["action today (expected unknown)"]


def test_the_set_has_50_cases_and_runs_end_to_end():
    results = asyncio.run(run(MockProvider()))

    assert len(CASES) == 50
    assert len(results) == 50
    assert any(case.items for case in CASES)
    assert report(results).splitlines()[-1].startswith("ผ่าน ")


def _plan(*commands):
    from app.ai.interpreter import Plan

    return Plan(tuple(commands))


def _add(title, day, hour=None):
    return Command(
        "add",
        title=title,
        due_at=datetime(2026, 10, 29, hour, 0, tzinfo=BKK) if hour else None,
        due_date=None if hour else day,
    )


def test_several_tasks_must_all_be_there_and_right():
    from datetime import date

    from scripts.eval_ai import check_plan

    case = Case(
        "x",
        "add",
        items=(
            Case("", "add", "2026-10-29", None, "ส่งรายงาน"),
            Case("", "add", "2026-10-29", "14:00", "ประชุม"),
        ),
    )
    day = date(2026, 10, 29)

    both = _plan(_add("ส่งรายงาน", day), _add("ประชุม", day, 14))
    assert check_plan(case, both) == []

    dropped = _plan(_add("ประชุม", day, 14))
    assert check_plan(case, dropped) == ["1 tasks (expected 2)"]

    wrong_time = _plan(_add("ส่งรายงาน", day), _add("ประชุม", day, 15))
    assert check_plan(case, wrong_time) == ["task 2: time 15:00 (expected 14:00)"]


def test_two_tasks_where_one_was_expected_is_wrong():
    from datetime import date

    from scripts.eval_ai import check_plan

    case = Case("x", "add", None, None, "นม")
    day = date(2026, 10, 29)

    plan = _plan(_add("นม", None), _add("ไข่", day))

    assert check_plan(case, plan) == ["2 tasks (expected 1)"]
