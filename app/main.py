from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import Settings, get_settings
from app.health import check_database, check_redis, run_checks
from app.line.webhook import router as line_router
from app.worker import enqueue_inbox


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=5,
            pool_pre_ping=True,
            connect_args={"timeout": 3},
        )
        redis = Redis.from_url(
            settings.redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        app.state.readiness_checks = {
            "database": partial(check_database, engine),
            "redis": partial(check_redis, redis),
        }
        yield
        await redis.aclose()
        await engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings
    app.state.enqueue_inbox = enqueue_inbox
    app.include_router(line_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    @app.get("/ready")
    async def ready(request: Request, response: Response) -> dict:
        results = await run_checks(
            request.app.state.readiness_checks, settings.readiness_timeout_seconds
        )
        ready = all(status == "ok" for status in results.values())
        response.status_code = 200 if ready else 503
        return {"status": "ready" if ready else "not_ready", "checks": results}

    return app


app = create_app()
