"""Celery worker: background jobs that run outside the web server.

Start it (Windows needs --pool=solo):
    celery -A app.worker worker --pool=solo --loglevel=info
Start the scheduler (runs the sweepers):
    celery -A app.worker beat --loglevel=info
"""

import asyncio
import logging
import os
import socket
from datetime import UTC, datetime

import httpx2
from celery import Celery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.ai.factory import build_provider
from app.bot.handler import make_handler
from app.config import get_settings
from app.db.models import User
from app.line.client import LineClient
from app.services.inbox_service import process_user_inbox, stale_line_user_ids
from app.services.outbox_service import deliver, due_outbox_ids

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery("noti", broker=settings.redis_url)
celery_app.conf.update(
    task_ignore_result=True,
    timezone="UTC",
    broker_connection_retry_on_startup=True,
    task_publish_retry=False,
    broker_transport_options={
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
        "max_retries": 0,
    },
    beat_schedule={
        "sweep-inbox-every-minute": {"task": "inbox.sweep", "schedule": 60.0},
        "send-due-outbox-every-15s": {"task": "outbox.sweep", "schedule": 15.0},
    },
)

WORKER_NAME = f"{socket.gethostname()}:{os.getpid()}"
handle_event = make_handler(build_provider(settings))


def _sessions():
    engine = create_async_engine(
        settings.database_url.get_secret_value(), poolclass=NullPool
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _send_due(sessions, user_id: int | None = None) -> int:
    """Deliver outbox rows that are due (for one user, or everyone)."""
    if settings.line_channel_access_token is None:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN not set; messages stay pending")
        return 0
    now = datetime.now(UTC)
    async with sessions() as session:
        ids = await due_outbox_ids(session, now, user_id)
    async with httpx2.AsyncClient(timeout=10) as http:
        line = LineClient(
            settings.line_channel_access_token.get_secret_value(),
            settings.line_api_base_url,
            http,
        )
        for outbox_id in ids:
            await deliver(sessions, outbox_id, line, datetime.now(UTC))
    return len(ids)


async def _process(line_user_id: str) -> int:
    engine, sessions = _sessions()
    try:
        handled = await process_user_inbox(
            sessions, line_user_id, owner=WORKER_NAME, handler=handle_event
        )
        async with sessions() as session:
            user_id = await session.scalar(
                select(User.id).where(User.line_user_id == line_user_id)
            )
        await _send_due(sessions, user_id)
        return handled
    finally:
        await engine.dispose()


async def _find_stale() -> list[str]:
    engine, sessions = _sessions()
    try:
        async with sessions() as session:
            return await stale_line_user_ids(session, datetime.now(UTC))
    finally:
        await engine.dispose()


async def _send_all_due() -> int:
    engine, sessions = _sessions()
    try:
        return await _send_due(sessions)
    finally:
        await engine.dispose()


@celery_app.task(name="inbox.process_user")
def process_user_inbox_task(line_user_id: str) -> int:
    return asyncio.run(_process(line_user_id))


@celery_app.task(name="inbox.sweep")
def sweep_inbox_task() -> int:
    line_user_ids = asyncio.run(_find_stale())
    for line_user_id in line_user_ids:
        process_user_inbox_task.delay(line_user_id)
    return len(line_user_ids)


@celery_app.task(name="outbox.sweep")
def sweep_outbox_task() -> int:
    return asyncio.run(_send_all_due())


def enqueue_inbox(line_user_id: str) -> None:
    """Wake a worker for this user. Called by the webhook after it commits."""
    process_user_inbox_task.delay(line_user_id)
