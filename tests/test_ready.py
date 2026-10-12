import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


async def ok() -> None:
    return None


async def broken() -> None:
    raise ConnectionError("password=secret host=db")


async def slow() -> None:
    await asyncio.sleep(5)


def call_ready(checks, **settings_overrides):
    settings = Settings(_env_file=None, **settings_overrides)
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.readiness_checks = checks
        return client.get("/ready")


def test_ready_all_200():
    response = call_ready({"database": ok, "redis": ok})

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "redis": "ok"},
    }


def test_ready_503_check_fail():
    response = call_ready({"database": broken, "redis": ok})

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "error: ConnectionError"
    assert "secret" not in response.text


def test_ready_503_timeout():
    response = call_ready(
        {"database": slow, "redis": ok}, readiness_timeout_seconds=0.1
    )

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "timeout"


def test_ready_with_real_services():
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200, response.json()


def test_ready_503_database_fail():
    wrong_port = "postgresql+asyncpg://planner:planner@localhost:1/planner"
    app = create_app(Settings(_env_file=None, database_url=wrong_port))

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "ok"
