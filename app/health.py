import asyncio
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

Check = Callable[[], Awaitable[None]]


async def check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_redis(client: Redis) -> None:
    await client.ping()


async def run_one(check: Check, timeout: float) -> str:
    try:
        await asyncio.wait_for(check(), timeout=timeout)

    except TimeoutError:
        return "timeout"

    except Exception as exc:
        return f"error: {type(exc).__name__}"

    return "ok"


async def run_checks(checks: dict[str, Check], timeout: float) -> dict[str, str]:
    names = list(checks)
    results = await asyncio.gather(*(run_one(checks[name], timeout) for name in names))
    return dict(zip(names, results, strict=True))
