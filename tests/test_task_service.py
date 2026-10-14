import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Task, User
from app.services import task_service as ts
from tests.helpers import database_url, run_in_rollback

def test_get_or_create_user_is_idempotent():
    async def scenario(session):
        first = await ts.get_or_create_user(session, "U-alice")
        second = await ts.get_or_create_user(session, "U-alice")
        count = await session.scalar(
            select(func.count()).select_from(User).where(User.line_user_id == "U-alice")
        )

        assert first.id == second.id
        assert count == 1

    run_in_rollback(scenario)

def test_task_numbers_run_per_user():
    async def scenario(session):
        alice = await ts.get_or_create_user(session, "U-alice")
        bob = await  ts.get_or_create_user(session, "U-bob")

        a1 = await ts.add_task(session, alice.id, "อ่าน paper")
        a2 = await ts.add_task(session, alice.id, "ส่งรายงาน")
        b1 = await ts.add_task(session, bob.id,  "ซื้อของ")

        assert (a1.task_no, a2.task_no, b1.task_no) == (1, 2, 1)
        assert a1.status == "todo"
        assert a1.priority == ts.MEDIUM

    run_in_rollback(scenario)

def test_add_task_cleans_title():
    async def scenario(session):
        user = await ts.get_or_create_user(session, "U-alice")
        task = await ts.add_task(session, user.id, "  อ่าน   paper \n")

        assert task.title == "อ่าน paper"

    run_in_rollback(scenario)

@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "   "},
        {"title": "x" * 201},
        {"title": "ok", "priority": 4},
        {"title": "ok", "estimated_minutes": 0},
        {
            "title": "ok",
            "due_date": date(2026, 11, 15),
            "due_at": datetime(2026, 11, 15, tzinfo=UTC),
        },
        {"title": "ok", "due_at": datetime(2026, 11, 15, 14, 0)}, #No TZ for test
    ],
)

def test_add_task_rejects_bad_input_without_using_a_number(kwargs):
    async def scenario(session):
        user = await ts.get_or_create_user(session, "U-alice")

        with pytest.raises(ts.InvalidTask):
            await ts.add_task(session, user.id, **kwargs)

        good = await ts.add_task(session, user.id, "งานถัดไป")
        assert good.task_no == 1

    run_in_rollback(scenario)

def test_complete_task_is_idempotent():
    async def scenario(session):
        user = await ts.get_or_create_user(session, "U-alice")
        await ts.add_task(session, user.id, "อ่าน paper")

        done = await ts.complete_task(session, user.id, 1)
        again = await ts.complete_task(session, user.id, 1)

        assert done.status == "done"
        assert done.completed_at is not None
        assert again.version == 2

    run_in_rollback(scenario)

def test_deleted_task_is_hidden_and_its_number_is_not_reused():
    async def scenario(session):
        user = await ts.get_or_create_user(session, "U-alice")
        await ts.add_task(session, user.id, "งานที่จะลบ")

        await ts.delete_task(session, user.id, 1)

        with pytest.raises(ts.TaskNotFound):
            await ts.get_task(session, user.id, 1)
        new = await ts.add_task(session, user.id, "งานใหม่")
        assert new.task_no == 2

    run_in_rollback(scenario)

def test_list_open_tasks_skips_done_and_deleted():
    async def scenario(session):
        user = await ts.get_or_create_user(session, "U-alice")
        for title in ["หนึ่ง", "สอง", "สาม", "สี่"]:
            await ts.add_task(session, user.id, title)
        await ts.complete_task(session, user.id, 2)
        await ts.delete_task(session, user.id, 3)

        tasks = await ts.list_open_tasks(session, user.id)

        assert [t.task_no for t in tasks] == [1, 4]

    run_in_rollback(scenario)

def test_users_cannot_touch_each_others_tasks():
    async def scenario(session):
        alice = await ts.get_or_create_user(session, "U-alice")
        bob = await  ts.get_or_create_user(session, "U-bob")

        await ts.add_task(session, alice.id, "งานลับของ Alice")

        with pytest.raises(ts.TaskNotFound):
            await ts.get_task(session, bob.id, 1)
        with pytest.raises(ts.TaskNotFound):
            await ts.complete_task(session, bob.id, 1)
        with pytest.raises(ts.TaskNotFound):
            await ts.delete_task(session, bob.id, 1)

        still_there = await ts.get_task(session, alice.id, 1)
        assert still_there.status == "todo"
        assert await ts.list_open_tasks(session, bob.id) == []

    run_in_rollback(scenario)

def test_concurrent_adds_get_unqiue_numbers():
    async def main():
        engine = create_async_engine(database_url())
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        line_user_id = f"U-concurrency-{uuid4()}"

        try: 
            async with sessions.begin() as session:
                user = await ts.get_or_create_user(session, line_user_id)

            async def add_one(i: int) -> int:
                async with sessions.begin() as session:
                    task = await ts.add_task(session, user.id, f"งาน {i}")
                    return task.task_no

            numbers = await asyncio.gather(*(add_one(i) for i in range(10)))

            assert sorted(numbers) == list(range(1, 11))

        finally:
            async with sessions.begin() as session:
                await session.execute(
                    delete(User).where(User.line_user_id == line_user_id)
                )
            await engine.dispose()

    asyncio.run(main())

def test_task_rows_really_exist_in_database():
    async def scenario(session):
        user = await ts.get_or_create_user(session, "U-alice")
        await ts.add_task(session, user.id, "อ่าน paper", estimated_minutes=60)

        row = await session.scalar(select(Task).where(Task.user_id == user.id))

        assert row.estimated_minutes == 60

    run_in_rollback(scenario)

