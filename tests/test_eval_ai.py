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


def test_hard_set_scores_apart_and_multi_task_cases_are_gaps():
    from scripts.eval_ai import HARD_CASES, SETS

    results = asyncio.run(run(MockProvider(), cases=HARD_CASES))
    text = report(results)

    assert SETS["all"] == CASES + HARD_CASES
    assert any(case.many for case in HARD_CASES)
    assert all(not r.ok for r in results if r.case.many)  # one answer, many tasks
    assert "ยังทำไม่ได้" in text
    assert "ข้าม" not in text  # gaps are not "skipped"
