"""The calendar API on real PostgreSQL. LINE's token check is replaced by a fake."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.line.liff_auth import InvalidToken, LiffUser
from app.main import create_app
from app.services import task_service as tasks
from app.services.dates import local_today
from tests.helpers import run_committed

TODAY = local_today(datetime.now(UTC), "Asia/Bangkok")


class FakeVerifier:
    """token "token-<line user id>" belongs to that user; anything else is rejected."""

    async def verify(self, id_token: str) -> LiffUser:
        if not id_token.startswith("token-"):
            raise InvalidToken(id_token)
        return LiffUser(id_token.removeprefix("token-"))


@pytest.fixture
def client():
    app = create_app(Settings(_env_file=None, liff_id="2001234567-Test"))
    app.state.liff_verifier = FakeVerifier()
    with TestClient(app) as test_client:
        yield test_client


def auth(line_user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer token-{line_user_id}"}


def add(line_user_id: str, title: str, **due) -> None:
    async def scenario(sessions):
        async with sessions.begin() as session:
            user = await tasks.get_or_create_user(session, line_user_id)
            await tasks.add_task(session, user.id, title, **due)

    run_committed(scenario)


def month(client, line_user_id: str, start: date = TODAY, days: int = 42):
    end = start + timedelta(days=days)
    return client.get(f"/api/tasks?start={start}&end={end}", headers=auth(line_user_id))


def test_config_and_page_are_public(client):
    assert client.get("/api/config").json() == {"liffId": "2001234567-Test"}
    page = client.get("/liff/")
    assert page.status_code == 200
    assert "app.js" in page.text


def test_tasks_need_a_valid_token(client, make_user_id):
    alice = make_user_id()
    start, end = TODAY, TODAY + timedelta(days=7)

    assert client.get(f"/api/tasks?start={start}&end={end}").status_code == 401
    bad = {"Authorization": "Bearer someone-elses-guess"}
    assert (
        client.get(f"/api/tasks?start={start}&end={end}", headers=bad).status_code
        == 401
    )
    assert month(client, alice).status_code == 200


def test_month_lists_own_tasks_with_local_date_and_time(client, make_user_id):
    alice, bob = make_user_id(), make_user_id()
    # 03:00 UTC = 10:00 in Bangkok
    at_ten = datetime.combine(TODAY, datetime.min.time(), UTC) + timedelta(hours=3)
    add(alice, "ประชุมทีม", due_at=at_ten, priority=3)
    add(alice, "อ่าน paper", due_date=TODAY + timedelta(days=1))
    add(alice, "นอกช่วง", due_date=TODAY + timedelta(days=60))
    add(bob, "งานของบ็อบ", due_date=TODAY)

    data = month(client, alice).json()

    assert data["today"] == TODAY.isoformat()
    assert [t["title"] for t in data["tasks"]] == ["ประชุมทีม", "อ่าน paper"]
    meeting = data["tasks"][0]
    assert meeting == {
        "number": 1,
        "title": "ประชุมทีม",
        "date": TODAY.isoformat(),
        "time": "10:00",
        "important": True,
        "done": False,
    }
    assert data["tasks"][1]["time"] is None  # all day


def test_overdue_comes_separately_whatever_the_range(client, make_user_id):
    alice = make_user_id()
    add(alice, "ส่งใบเบิก", due_date=TODAY - timedelta(days=40))

    data = month(client, alice, start=TODAY, days=7).json()

    assert data["tasks"] == []
    assert [t["title"] for t in data["overdue"]] == ["ส่งใบเบิก"]


def test_done_and_undo(client, make_user_id):
    alice = make_user_id()
    add(alice, "อ่าน paper", due_date=TODAY)

    done = client.post("/api/tasks/1/done", headers=auth(alice))
    assert done.json()["done"] is True
    assert month(client, alice).json()["tasks"][0]["done"] is True  # still listed

    undone = client.post("/api/tasks/1/undo", headers=auth(alice))
    assert undone.json()["done"] is False


def test_cannot_touch_someone_elses_task(client, make_user_id):
    alice, bob = make_user_id(), make_user_id()
    add(alice, "งานของอลิซ", due_date=TODAY)

    assert client.post("/api/tasks/1/done", headers=auth(bob)).status_code == 404
    assert month(client, alice).json()["tasks"][0]["done"] is False


def test_range_is_limited(client, make_user_id):
    alice = make_user_id()

    assert month(client, alice, days=0).status_code == 422
    assert month(client, alice, days=400).status_code == 422
