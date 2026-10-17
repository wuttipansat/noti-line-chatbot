import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.config import Settings


def database_url() -> str:
    return Settings(_env_file=None).database_url.get_secret_value()


def run_in_rollback(scenario):

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


def run_committed(scenario):
    async def main():
        engine = create_async_engine(database_url())
        try:
            await scenario(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()

    asyncio.run(main())
