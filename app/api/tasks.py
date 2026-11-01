"""JSON API for the LIFF calendar page.

Every request must carry "Authorization: Bearer <LIFF ID token>". We ask LINE who
the token belongs to (app/line/liff_auth.py) and only ever touch that user's tasks.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.replies import task_day
from app.db.models import Task, User
from app.line.liff_auth import InvalidToken
from app.services import task_service as tasks
from app.services.dates import local_now, local_today

router = APIRouter(prefix="/api")
MAX_RANGE_DAYS = 62  # a month grid is 42 days; refuse anything much bigger


class TaskOut(BaseModel):
    number: int
    title: str
    date: date | None
    time: str | None  # "10:00" local time, or None = all day
    important: bool
    done: bool


class TasksOut(BaseModel):
    today: date  # the user's local date: the page highlights it
    tasks: list[TaskOut]  # due in the asked range, done ones too
    overdue: list[TaskOut]  # still open and due before today, from any date


@dataclass
class Account:
    session: AsyncSession
    user: User


def task_out(task: Task, tz: str) -> TaskOut:
    clock = None
    if task.due_at is not None:
        clock = local_now(task.due_at, tz).strftime("%H:%M")
    return TaskOut(
        number=task.task_no,
        title=task.title,
        date=task_day(task, tz),
        time=clock,
        important=task.priority == 3,
        done=task.status == "done",
    )


async def account(request: Request) -> AsyncIterator[Account]:
    """Check the LIFF ID token, then open one transaction for this request."""
    verifier = request.app.state.liff_verifier
    if verifier is None:
        raise HTTPException(503, "LIFF is not configured on the server")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "missing token")
    try:
        liff_user = await verifier.verify(token)
    except InvalidToken:
        raise HTTPException(401, "invalid or expired token") from None

    async with request.app.state.sessionmaker.begin() as session:
        user = await tasks.get_or_create_user(session, liff_user.line_user_id)
        yield Account(session, user)


CurrentAccount = Annotated[Account, Depends(account)]


@router.get("/config")
async def config(request: Request) -> dict[str, str | None]:
    """Public settings the page needs before it can start LIFF."""
    return {"liffId": request.app.state.settings.liff_id}


@router.get("/tasks")
async def list_tasks(
    me: CurrentAccount,
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
) -> TasksOut:
    """Tasks due from start up to (not including) end, done ones too."""
    if not timedelta(0) < end - start <= timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(422, f"range must be 1-{MAX_RANGE_DAYS} days")
    now = datetime.now(UTC)
    found = await tasks.tasks_between(me.session, me.user.id, start, end)
    late = (await tasks.today_view(me.session, me.user.id, now)).overdue
    tz = me.user.timezone
    return TasksOut(
        today=local_today(now, tz),
        tasks=[task_out(t, tz) for t in found],
        overdue=[task_out(t, tz) for t in late],
    )


async def _change(me: Account, number: int, action) -> TaskOut:
    try:
        task = await action(me.session, me.user.id, number)
    except tasks.TaskNotFound:
        raise HTTPException(404, f"task #{number} not found") from None
    return task_out(task, me.user.timezone)


@router.post("/tasks/{number}/done")
async def mark_done(me: CurrentAccount, number: int) -> TaskOut:
    return await _change(me, number, tasks.complete_task)


@router.post("/tasks/{number}/undo")
async def mark_not_done(me: CurrentAccount, number: int) -> TaskOut:
    return await _change(me, number, tasks.reopen_task)
