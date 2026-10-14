from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services import task_service as ts
from tests.helpers import run_in_rollback

BKK = ZoneInfo("Asia/Bangkok")


def bkk(*args: int) -> datetime:
    return datetime(*args, tzinfo=BKK)


def numbers(tasks) -> list[int]:
    return [t.task_no for t in tasks]


def test_views_respect_user_timezone():
    async def scenario(session):
        user = await ts.get_or_create_user(session, "U-alice")
        await ts.add_task(session, user.id, "due 14", due_date=date(2026, 10, 14))
        await ts.add_task(session, user.id, "due 15", due_date=date(2026, 10, 15))
        await ts.add_task(
            session, user.id, "14 at 23:00", due_at=bkk(2026, 10, 14, 23, 0)
        )
        await ts.add_task(
            session, user.id, "15 at 00:15", due_at=bkk(2026, 10, 15, 0, 15)
        )

        before = await ts.today_view(session, user.id, bkk(2026, 10, 14, 23, 30))
        assert before.local_date == date(2026, 10, 14)
        assert numbers(before.overdue) == []
        assert numbers(before.today) == [3, 1]

        after = await ts.today_view(session, user.id, bkk(2026, 10, 15, 0, 30))
        assert after.local_date == date(2026, 10, 15)
        assert numbers(after.overdue) == [3, 1]
        assert numbers(after.today) == [4, 2]

    run_in_rollback(scenario)


def test_today_view_skips_done_deleted_and_undated():
    async def scenario(session):
        user = await ts.get_or_create_user(session, "U-alice")
        for title in ["done", "deleted", "open"]:
            await ts.add_task(session, user.id, title, due_date=date(2026, 10, 14))
        await ts.add_task(session, user.id, "no deadline")
        await ts.complete_task(session, user.id, 1)
        await ts.delete_task(session, user.id, 2)

        view = await ts.today_view(session, user.id, bkk(2026, 10, 14, 9, 0))

        assert numbers(view.today) == [3]
        assert numbers(view.overdue) == []

    run_in_rollback(scenario)


def test_week_view_covers_seven_local_days_in_deadline_order():
    async def scenario(session):
        user = await ts.get_or_create_user(session, "U-alice")
        await ts.add_task(session, user.id, "yesterday", due_date=date(2026, 10, 13))
        await ts.add_task(session, user.id, "day 7", due_date=date(2026, 10, 20))
        await ts.add_task(session, user.id, "day 8", due_date=date(2026, 10, 21))
        await ts.add_task(session, user.id, "today", due_date=date(2026, 10, 14))
        await ts.add_task(
            session, user.id, "day 3 at 9", due_at=bkk(2026, 10, 16, 9, 0)
        )
        await ts.add_task(session, user.id, "day 3", due_date=date(2026, 10, 16))

        week = await ts.week_view(session, user.id, bkk(2026, 10, 14, 9, 0))

        assert numbers(week) == [4, 5, 6, 2]

    run_in_rollback(scenario)


def test_views_only_show_own_tasks():
    async def scenario(session):
        alice = await ts.get_or_create_user(session, "U-alice")
        bob = await ts.get_or_create_user(session, "U-bob")
        await ts.add_task(session, alice.id, "Alice's", due_date=date(2026, 10, 14))

        now = bkk(2026, 10, 14, 9, 0)
        view = await ts.today_view(session, bob.id, now)

        assert view.today == []
        assert await ts.week_view(session, bob.id, now) == []

    run_in_rollback(scenario)
