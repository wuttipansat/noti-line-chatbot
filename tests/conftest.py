"""Fixtures shared by every test file (pytest loads this file automatically)."""

from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.db.models import InboundEvent, User
from tests.helpers import run_committed


@pytest.fixture
def make_user_id():
    """Call it to get a fresh LINE user id; all of them are deleted afterwards."""
    created: list[str] = []

    def make() -> str:
        created.append(f"U-bot-{uuid4()}")
        return created[-1]

    yield make

    async def cleanup(sessions):
        async with sessions.begin() as session:
            await session.execute(
                delete(InboundEvent).where(InboundEvent.line_user_id.in_(created))
            )
            await session.execute(delete(User).where(User.line_user_id.in_(created)))

    run_committed(cleanup)
