"""Celery worker: background jobs that run outside the web server.

Start 
    celery -A app.worker worker --pool=solo --loglevel=info
Start the scheduler
    celery -A app.worker beat --loglevel=info
"""

import asyncio
import os
import socket
from datetime import UTC, datetime

from celery import Celery
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.services.inbox_service import process_user_inbox, stale_line_user_ids

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
        "max_retries": 0
    },
    beat_schedule={
        "sweep-inbox-every-minute": {
            "task": "inbox.sweep",
            "schedule": 60.0,
        },
    },
)

WORKER_NAME = f"{socket.gethostname()}:{os.getpid()}"

def _sessions():
    engine = create_async_engine(
        settings.database_url.get_secret_value(), poolclass=NullPool
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)

async def _process(line_user_id: str) -> int:
    engine, sessions = _sessions()
    try:
        return await process_user_inbox(sessions, line_user_id, owner=WORKER_NAME)

    finally:
        await engine.dispose()

async def _find_stale() -> list[str]:
    engine, sessions = _sessions()
    try:
        async with sessions() as session:
            return await stale_line_user_ids(session, datetime.now(UTC))

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

def enqueue_inbox(line_user_id: str) -> None:
    process_user_inbox_task.delay(line_user_id)

    