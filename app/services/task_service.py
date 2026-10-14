from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Task, User

LOW, MEDIUM, HIGH = 1, 2, 3
MAX_TITLE_LENGTH = 200
MAX_TIME_MINUTES = 1440

class TaskNotFound(Exception):
    """Task #n does not exist for this user, or it has been deleted"""

    def __init__(self, task_no: int) -> None:
        super().__init__(f"task #{task_no} not found")
        self.task_no = task_no

class InvalidTask(ValueError):
    """Task data breaks a rule. Checked before hitting the DB for a clearer error."""

def _now() -> datetime:
    return datetime.now(UTC)

def _clean_title(title: str) -> str:
    cleaned = " ".join(title.split())
    if not cleaned:
        raise InvalidTask("title is empty")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise InvalidTask(f"title is longer than {MAX_TITLE_LENGTH} characters")
    return cleaned

def _check_fields(
        priority: int,
        estimated_minutes: int | None,
        due_date: date | None,
        due_at: datetime | None,
) -> None:
    if priority not in (LOW, MEDIUM, HIGH):
        raise InvalidTask(f"priority must be {LOW}, {MEDIUM}, or {HIGH}")
    if estimated_minutes is not None and not 1 <= estimated_minutes <= MAX_TIME_MINUTES:
        raise InvalidTask(f"estimated_minutes must be between 1 and {MAX_TIME_MINUTES}")
    if due_date is not None and due_at is not None:
        raise InvalidTask("use due_date or due_at, not both")
    if due_at is not None and due_at.tzinfo is None:
        raise InvalidTask("due_at must be timezone-aware")

async def get_or_create_user(session: AsyncSession, line_user_id: str) -> User:
    await session.execute(
        pg_insert(User)
        .values(line_user_id=line_user_id)
        .on_conflict_do_nothing(index_elements=[User.line_user_id])
    )
    result = await session.execute(
        select(User).where(User.line_user_id == line_user_id)
    )

    return result.scalar_one()

async def _take_next_task_no(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(next_task_no=User.next_task_no + 1)
        .returning(User.next_task_no - 1)
    )

    return result.scalar_one()

async def add_task(
        session: AsyncSession,
        user_id: int,
        title: str,
        *,
        description: str | None = None,
        priority: int = MEDIUM,
        estimated_minutes: int | None = None,
        due_date: date | None = None,
        due_at: datetime | None = None,
) -> Task:
    title = _clean_title(title)
    _check_fields(priority, estimated_minutes, due_date, due_at)

    task =Task(
        user_id=user_id,
        task_no=await _take_next_task_no(session, user_id),
        title=title,
        description=description,
        priority=priority,
        estimated_minutes=estimated_minutes,
        due_date=due_date,
        due_at=due_at,
    )
    session.add(task)
    await session.flush()
    return task

async def get_task(session: AsyncSession, user_id: int, task_no: int) -> Task:
    result = await session.execute(
        select(Task).where(
            Task.user_id == user_id,
            Task.task_no == task_no,
            Task.deleted_at.is_(None),
        )
    )

    task = result.scalar_one_or_none()
    if task is None:
        raise TaskNotFound(task_no)
    return task

async def list_open_tasks(session: AsyncSession, user_id: int) -> list[Task]:
    result = await session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.status != "done",
            Task.deleted_at.is_(None),
        )
    )
    return list(result.scalars())

async def complete_task(session: AsyncSession, user_id: int, task_no: int) -> Task:
    task = await get_task(session, user_id, task_no)
    if task.status != "done":
        task.status = "done"
        task.completed_at = _now()
        task.version += 1
        await session.flush()
    return task

async def delete_task(session: AsyncSession, user_id: int, task_no: int) -> None:
    task = await get_task(session, user_id, task_no)
    task.deleted_at = _now()
    task.version += 1
    await session.flush()
