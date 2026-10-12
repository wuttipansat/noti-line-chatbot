"""ทดสอบกับ PostgreSQL จริง — ต้องรัน `docker compose up -d` และ `alembic upgrade head` ก่อน"""

import asyncio
from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.config import Settings
from app.db.models import Task, User


def run_in_rollback(scenario):
    """รัน scenario ใน transaction แล้ว rollback ทิ้ง → DB สะอาดเหมือนเดิมทุกครั้ง"""

    async def main():
        url = Settings(_env_file=None).database_url.get_secret_value()
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                outer = await conn.begin()
                session = AsyncSession(
                    bind=conn, join_transaction_mode="create_savepoint"
                )
                try:
                    await scenario(session)
                finally:
                    await session.close()
                    await outer.rollback()
        finally:
            await engine.dispose()

    asyncio.run(main())


async def make_user(session: AsyncSession, line_user_id: str = "U-test-a") -> User:
    user = User(line_user_id=line_user_id)
    session.add(user)
    await session.flush()
    return user


def test_database_matches_models():
    command.check(Config("alembic.ini"))


def test_defaults_are_filled_by_database():
    async def scenario(session):
        user = await make_user(session)
        task = Task(user_id=user.id, task_no=1, title="อ่าน paper")
        session.add(task)
        await session.flush()
        await session.refresh(user)
        await session.refresh(task)

        assert user.timezone == "Asia/Bangkok"
        assert user.next_task_no == 1
        assert user.is_active is True
        assert task.status == "todo"
        assert task.priority == 2
        assert task.version == 1
        assert task.created_at.tzinfo is not None

    run_in_rollback(scenario)


def test_task_no_is_unique_per_user_only():
    async def scenario(session):
        a = await make_user(session, "U-test-a")
        b = await make_user(session, "U-test-b")
        session.add_all(
            [
                Task(user_id=a.id, task_no=1, title="งานของ A"),
                Task(user_id=b.id, task_no=1, title="งานของ B"),
            ]
        )
        await session.flush()  # คนละ user ใช้ #1 ได้ → ไม่ error

        session.add(Task(user_id=a.id, task_no=1, title="ซ้ำ"))
        with pytest.raises(IntegrityError, match="uq_tasks_user_id_task_no"):
            await session.flush()  # user เดียวกันใช้ #1 ซ้ำ → error

    run_in_rollback(scenario)


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        (
            {
                "due_date": date(2026, 11, 15),
                "due_at": datetime(2026, 11, 15, tzinfo=UTC),
            },
            "ck_tasks_one_deadline",
        ),
        ({"priority": 4}, "ck_tasks_priority_range"),
        ({"estimated_minutes": 0}, "ck_tasks_estimated_minutes_range"),
        ({"status": "doing"}, "ck_tasks_status_allowed"),
    ],
)
def test_check_constraints_reject_bad_data(fields, constraint):
    async def scenario(session):
        user = await make_user(session)
        session.add(Task(user_id=user.id, task_no=1, title="x", **fields))
        with pytest.raises(IntegrityError, match=constraint):
            await session.flush()

    run_in_rollback(scenario)


def test_deleting_user_deletes_their_tasks():
    async def scenario(session):
        user = await make_user(session)
        session.add(Task(user_id=user.id, task_no=1, title="x"))
        await session.flush()

        await session.execute(delete(User).where(User.id == user.id))
        count = await session.scalar(
            select(func.count()).select_from(Task).where(Task.user_id == user.id)
        )

        assert count == 0

    run_in_rollback(scenario)