"""The bot end to end (minus LINE): message in -> task change + reply in outbox.

Runs the real inbox processing against PostgreSQL. Each test cleans up its users.
"""

import json
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy import text as text_sql

from app.bot.handler import SLOW_DOWN, handle_event, make_handler
from app.db.models import InboundEvent, OutboundMessage, Task, User
from app.services import task_service as tasks
from app.services.inbox_service import process_user_inbox
from app.services.limit_service import Limits
from tests.helpers import run_committed

BKK = ZoneInfo("Asia/Bangkok")
NOON = datetime(2026, 10, 15, 12, 0, tzinfo=BKK)
EARLY = datetime(2026, 10, 15, 4, 0, tzinfo=BKK)  # before every time a test asks about


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


async def send(sessions, line_user_id, payload, sent_at, handler=handle_event):
    """Pretend LINE delivered one event, then let the worker process it."""
    async with sessions.begin() as session:
        session.add(
            InboundEvent(
                webhook_event_id=f"evt-{uuid4()}",
                line_user_id=line_user_id,
                event_type=payload["type"],
                event_ts=sent_at,
                reply_token=f"tok-{uuid4()}",
                payload=payload,
            )
        )
    await process_user_inbox(sessions, line_user_id, "w1", handler=handler)


async def say(sessions, line_user_id, text, sent_at=NOON, handler=handle_event):
    message = {"type": "message", "message": {"type": "text", "text": text}}
    await send(sessions, line_user_id, message, sent_at, handler)


async def replies_to(sessions, line_user_id: str) -> list[OutboundMessage]:
    async with sessions() as session:
        rows = await session.scalars(
            select(OutboundMessage)
            .join(User, User.id == OutboundMessage.user_id)
            .where(User.line_user_id == line_user_id)
            .order_by(OutboundMessage.id)
        )
        return list(rows)


def texts(rows: list[OutboundMessage]) -> list[str]:
    """The text of each reply. A Flex card has no "text": use its altText."""
    return [row.messages[0].get("text") or row.messages[0]["altText"] for row in rows]


def test_add_then_done(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(sessions, alice, "#1 เสร็จ", NOON + timedelta(seconds=5))

        rows = await replies_to(sessions, alice)
        assert texts(rows) == ["✅ เพิ่มงาน #1 อ่าน paper", "✅ #1 อ่าน paper เสร็จแล้ว"]
        assert [row.critical for row in rows] == [True, True]
        async with sessions() as session:
            task = await session.scalar(
                select(Task).join(User).where(User.line_user_id == alice)
            )
        assert task.status == "done"

    run_committed(scenario)


def test_done_unknown_task(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "#9 เสร็จ")

        [row] = await replies_to(sessions, alice)
        assert row.messages[0]["text"] == "ไม่พบงาน #9 ครับ"
        assert row.critical is False

    run_committed(scenario)


def test_add_without_title_asks_for_one(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน")

        assert texts(await replies_to(sessions, alice)) == [
            "กรุณาพิมพ์ชื่องานด้วยครับ เช่น: เพิ่มงาน อ่าน paper"
        ]

    run_committed(scenario)


def test_today_uses_the_time_the_user_sent_the_message(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        async with sessions.begin() as session:
            user = await tasks.get_or_create_user(session, alice)
            await tasks.add_task(
                session, user.id, "งานวันที่ 16", due_date=date(2026, 10, 16)
            )

        await say(sessions, alice, "งานวันนี้", datetime(2026, 10, 16, 0, 30, tzinfo=BKK))

        [body] = texts(await replies_to(sessions, alice))
        assert body.startswith("📋 งานวันนี้ (ศ. 16 ต.ค.)")
        assert "#1 งานวันที่ 16" in body

    run_committed(scenario)


def test_users_cannot_complete_each_others_tasks(make_user_id):
    alice, bob = make_user_id(), make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน งานลับของ Alice")
        await say(sessions, bob, "#1 เสร็จ")

        assert texts(await replies_to(sessions, bob)) == ["ไม่พบงาน #1 ครับ"]
        async with sessions() as session:
            task = await session.scalar(
                select(Task).join(User).where(User.line_user_id == alice)
            )
        assert task.status == "todo"

    run_committed(scenario)


def test_unknown_text_gets_a_hint_with_buttons(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "สวัสดีครับ")

        [row] = await replies_to(sessions, alice)
        assert "วิธีใช้" in row.messages[0]["text"]
        assert len(row.messages[0]["quickReply"]["items"]) == 3

    run_committed(scenario)


def test_follow_gets_a_welcome_and_stickers_get_nothing(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await send(sessions, alice, {"type": "follow"}, NOON)
        sticker = {"type": "message", "message": {"type": "sticker", "stickerId": "1"}}
        await send(sessions, alice, sticker, NOON + timedelta(seconds=1))

        [body] = texts(await replies_to(sessions, alice))
        assert body.startswith("สวัสดีครับ 👋")

    run_committed(scenario)


class ScriptedModel:
    """A fake language model that always gives the same answer."""

    name = "scripted"

    def __init__(self, answer: str):
        self.answer = answer

    async def extract(self, text, context):
        return self.answer


def test_natural_language_task_with_deadline(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "พรุ่งนี้บ่ายสองส่งรายงาน")

        [row] = await replies_to(sessions, alice)
        assert row.messages[0]["text"] == "✅ เพิ่มงาน #1 ส่งรายงาน · ศ. 16 ต.ค. 14:00"
        assert row.critical is True

    run_committed(scenario)


def test_bad_model_answer_changes_nothing(make_user_id):
    alice = make_user_id()
    broken = make_handler(ScriptedModel('{"action": "add", "title": "x", "hack": 1}'))

    async def scenario(sessions):
        await say(sessions, alice, "ช่วยจดงานหน่อย", handler=broken)

        assert "ยังไม่เข้าใจ" in texts(await replies_to(sessions, alice))[0]
        async with sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Task)
                .join(User)
                .where(User.line_user_id == alice)
            )
        assert count == 0

    run_committed(scenario)


def test_model_cannot_complete_another_users_task(make_user_id):
    alice, bob = make_user_id(), make_user_id()
    sneaky = make_handler(ScriptedModel('{"action": "done", "task_no": 1}'))

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน งานของ Alice")
        await say(sessions, bob, "ปิดงานแรกให้หน่อย", handler=sneaky)

        assert "ยังไม่เข้าใจ" in texts(await replies_to(sessions, bob))[0]
        async with sessions() as session:
            task = await session.scalar(
                select(Task).join(User).where(User.line_user_id == alice)
            )
        assert task.status == "todo"

    run_committed(scenario)


async def tasks_of(sessions, line_user_id: str) -> list[Task]:
    async with sessions() as session:
        rows = await session.scalars(
            select(Task).join(User).where(User.line_user_id == line_user_id)
        )
        return list(rows)


def buttons(row: OutboundMessage) -> list[str]:
    return [i["action"]["text"] for i in row.messages[0]["quickReply"]["items"]]


def test_ambiguous_time_asks_first_then_saves_the_answer(make_user_id):
    """AC-11: ask back, and write nothing until the user answers."""
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "หกโมงประชุมทีม", EARLY)

        [question] = await replies_to(sessions, alice)
        assert question.messages[0]["text"] == "⏰ ประชุมทีม (พฤ. 15 ต.ค.) กี่โมงครับ?"
        assert buttons(question) == ["06:00", "18:00", "ยกเลิก"]
        assert await tasks_of(sessions, alice) == []

        await say(sessions, alice, "18:00", EARLY + timedelta(minutes=1))

        [task] = await tasks_of(sessions, alice)
        assert task.title == "ประชุมทีม"
        assert task.due_at == datetime(2026, 10, 15, 18, 0, tzinfo=BKK)
        answer = (await replies_to(sessions, alice))[-1]
        assert answer.messages[0]["text"].startswith("✅ เพิ่มงาน #1 ประชุมทีม")
        assert answer.critical is True

    run_committed(scenario)


def test_pending_question_belongs_to_one_user(make_user_id):
    """AC-12: Bob's "18:00" must not answer Alice's question."""
    alice, bob = make_user_id(), make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "หกโมงประชุมทีม", EARLY)
        await say(sessions, bob, "18:00", EARLY + timedelta(seconds=5))
        await say(sessions, alice, "06:00", EARLY + timedelta(seconds=10))

        assert await tasks_of(sessions, bob) == []
        [task] = await tasks_of(sessions, alice)
        assert task.due_at == datetime(2026, 10, 15, 6, 0, tzinfo=BKK)

    run_committed(scenario)


def test_cancel_clears_the_question(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "หกโมงประชุมทีม", EARLY)
        await say(sessions, alice, "ยกเลิก", EARLY + timedelta(seconds=5))
        await say(sessions, alice, "18:00", EARLY + timedelta(seconds=10))

        assert await tasks_of(sessions, alice) == []
        assert texts(await replies_to(sessions, alice))[1] == "ยกเลิกแล้วครับ"

    run_committed(scenario)


def test_expired_question_is_not_answered(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "หกโมงประชุมทีม", EARLY)
        await say(sessions, alice, "18:00", EARLY + timedelta(minutes=11))

        assert await tasks_of(sessions, alice) == []
        assert texts(await replies_to(sessions, alice))[-1].startswith(
            "คำถามนั้นหมดเวลาแล้ว"
        )

    run_committed(scenario)


def test_new_topic_drops_the_question(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "หกโมงประชุมทีม", EARLY)
        await say(sessions, alice, "เพิ่มงาน อ่าน paper", EARLY + timedelta(seconds=5))
        await say(sessions, alice, "18:00", EARLY + timedelta(seconds=10))

        assert [t.title for t in await tasks_of(sessions, alice)] == ["อ่าน paper"]

    run_committed(scenario)


class CountingModel:
    """A pretend model that never understands, and counts how often it is asked."""

    name = "counting"

    def __init__(self):
        self.calls = 0

    async def extract(self, text, context):
        self.calls += 1
        return '{"action": "unknown"}'


def test_too_many_messages_get_one_warning_then_silence(make_user_id):
    """AC-28: over the limit -> one warning, then no replies and no AI."""
    alice, bob = make_user_id(), make_user_id()
    model = CountingModel()
    strict = make_handler(model, Limits(messages=3, window=timedelta(minutes=10)))

    async def scenario(sessions):
        for i in range(5):
            await say(sessions, alice, "หวัดดี", NOON + timedelta(seconds=i), strict)
        await say(sessions, bob, "หวัดดี", NOON + timedelta(seconds=5), strict)

        assert texts(await replies_to(sessions, alice)) == [
            'ยังไม่เข้าใจครับ 🙏 ลองพิมพ์ "วิธีใช้"',
            'ยังไม่เข้าใจครับ 🙏 ลองพิมพ์ "วิธีใช้"',
            'ยังไม่เข้าใจครับ 🙏 ลองพิมพ์ "วิธีใช้"',
            SLOW_DOWN,
        ]  # the 5th message: no reply at all
        assert model.calls == 3 + 1  # 3 from alice, 1 from bob
        assert len(await replies_to(sessions, bob)) == 1  # bob is not limited

        # 11 minutes later the window has passed: back to normal
        await say(sessions, alice, "งานวันนี้", NOON + timedelta(minutes=11), strict)
        assert len(await replies_to(sessions, alice)) == 5

    run_committed(scenario)


def test_daily_ai_budget_then_rules_only(make_user_id):
    alice = make_user_id()
    model = CountingModel()
    thrifty = make_handler(model, Limits(ai_per_day=2))

    async def scenario(sessions):
        for i in range(3):
            await say(sessions, alice, "หวัดดี", NOON + timedelta(seconds=i), thrifty)
        assert model.calls == 2  # the 3rd message went to the free rules only

        # Rules still work without the model:
        await say(
            sessions, alice, "พรุ่งนี้บ่ายสองส่งรายงาน", NOON + timedelta(seconds=5), thrifty
        )
        assert texts(await replies_to(sessions, alice))[-1].startswith("✅ เพิ่มงาน")
        assert model.calls == 2

        await say(sessions, alice, "หวัดดี", NOON + timedelta(days=1), thrifty)
        assert model.calls == 3  # a new day: a new budget

    run_committed(scenario)


def test_fixed_commands_do_not_use_the_ai_budget(make_user_id):
    alice = make_user_id()
    model = CountingModel()
    thrifty = make_handler(model, Limits(ai_per_day=1))

    async def scenario(sessions):
        await say(sessions, alice, "งานวันนี้", NOON, thrifty)
        await say(
            sessions, alice, "เพิ่มงาน อ่าน paper", NOON + timedelta(seconds=1), thrifty
        )
        await say(sessions, alice, "หวัดดี", NOON + timedelta(seconds=2), thrifty)

        assert model.calls == 1

    run_committed(scenario)


def scripted(answer: dict) -> ScriptedModel:
    return ScriptedModel(json.dumps(answer, ensure_ascii=False))


def add(title, due_date=None, **more) -> dict:
    return {"action": "add", "title": title, "due_date": due_date, **more}


def test_several_tasks_in_one_message_get_one_reply_listing_all(make_user_id):
    alice = make_user_id()
    model = scripted(
        {
            "commands": [
                add("ส่งรายงาน", "2026-10-16"),
                add("ประชุมทีม", "2026-10-17", due_time="14:00"),
            ]
        }
    )

    async def scenario(sessions):
        handler = make_handler(model)
        await say(sessions, alice, "พรุ่งนี้ส่งรายงาน แล้วศุกร์ประชุมทีม", handler=handler)

        [row] = await replies_to(sessions, alice)
        body = row.messages[0]["text"]
        assert body.startswith("📝 จัดการให้ 2 รายการ")
        assert "➕ #1 ส่งรายงาน" in body
        assert "➕ #2 ประชุมทีม" in body
        assert "⚠" not in body
        assert row.critical is True
        async with sessions() as session:
            saved = await session.scalars(
                select(Task.title).join(User).where(User.line_user_id == alice)
            )
        assert sorted(saved) == sorted(["ส่งรายงาน", "ประชุมทีม"])

    run_committed(scenario)


def test_done_and_add_in_one_message(make_user_id):
    alice = make_user_id()
    model = scripted(
        {"commands": [{"action": "done", "task_no": 1}, add("ประชุม", "2026-10-16")]}
    )

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(
            sessions,
            alice,
            "อ่านจบแล้ว พรุ่งนี้ประชุม",
            NOON + timedelta(seconds=5),
            make_handler(model),
        )

        body = texts(await replies_to(sessions, alice))[-1]
        assert "✅ #1 อ่าน paper เสร็จแล้ว" in body
        assert "➕ #2 ประชุม" in body

    run_committed(scenario)


def test_a_part_that_cannot_be_saved_is_named_in_the_reply(make_user_id):
    alice = make_user_id()
    model = scripted(
        {"commands": [add("ส่งรายงาน", "2026-10-16"), add("ประชุมผี", "2026-02-30")]}
    )

    async def scenario(sessions):
        await say(sessions, alice, "สองงาน", handler=make_handler(model))

        [row] = await replies_to(sessions, alice)
        body = row.messages[0]["text"]
        assert "➕ #1 ส่งรายงาน" in body
        assert "ไม่เข้าใจอีก 1 รายการ: “ประชุมผี”" in body  # never silently lost

    run_committed(scenario)


def test_over_the_limit_is_saved_in_part_and_the_rest_is_reported(make_user_id):
    alice = make_user_id()
    items = [add(f"งาน {n}", "2026-10-16") for n in range(7)]

    async def scenario(sessions):
        handler = make_handler(scripted({"commands": items}))
        await say(sessions, alice, "เจ็ดงาน", handler=handler)

        body = texts(await replies_to(sessions, alice))[0]
        assert "จัดการให้ 5 รายการ" in body
        assert "ส่งได้ทีละ 5 งาน" in body
        assert "“งาน 5”, “งาน 6”" in body
        async with sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Task)
                .join(User)
                .where(User.line_user_id == alice)
            )
        assert count == 5

    run_committed(scenario)


def test_nothing_understood_gives_the_usual_answer(make_user_id):
    alice = make_user_id()
    model = scripted({"commands": [add("ก", "2026-02-30"), add("ข", "2026-02-31")]})

    async def scenario(sessions):
        await say(sessions, alice, "สองงานที่พัง", handler=make_handler(model))

        [body] = texts(await replies_to(sessions, alice))
        assert body.startswith("ยังไม่เข้าใจครับ")

    run_committed(scenario)


def test_one_time_question_in_a_batch_is_asked_after_the_others_are_saved(make_user_id):
    alice = make_user_id()
    model = scripted(
        {
            "commands": [
                add("ส่งรายงาน", "2026-10-16"),
                add("ประชุมทีม", "2026-10-16", time_options=["06:00", "18:00"]),
            ]
        }
    )

    async def scenario(sessions):
        handler = make_handler(model)
        await say(sessions, alice, "สองงาน", handler=handler)
        await say(
            sessions, alice, "18:00", NOON + timedelta(seconds=5), handler=handler
        )

        first, second = texts(await replies_to(sessions, alice))
        assert "➕ #1 ส่งรายงาน" in first
        assert "⏰ ประชุมทีม" in first and "กี่โมงครับ?" in first
        assert "ประชุมทีม" in second

    run_committed(scenario)


async def open_titles(sessions, line_user_id) -> list[str]:
    async with sessions() as session:
        user = await tasks.get_or_create_user(session, line_user_id)
        return [t.title for t in await tasks.list_open_tasks(session, user.id)]


def test_delete_asks_first_and_deletes_only_after_the_button(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(sessions, alice, "ลบ 1", NOON + timedelta(seconds=5))

        assert await open_titles(sessions, alice) == ["อ่าน paper"]  # still there
        ask = (await replies_to(sessions, alice))[-1]
        assert "ลบงาน #1 อ่าน paper ใช่ไหมครับ?" in ask.messages[0]["text"]
        labels = [i["action"]["label"] for i in ask.messages[0]["quickReply"]["items"]]
        assert labels == ["ยืนยันลบ #1", "ยกเลิก"]

        await say(sessions, alice, "ยืนยันลบ #1", NOON + timedelta(seconds=10))

        assert await open_titles(sessions, alice) == []
        assert "🗑 ลบ #1 อ่าน paper แล้ว" in texts(await replies_to(sessions, alice))[-1]

    run_committed(scenario)


def test_cancelling_a_delete_keeps_the_task(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(sessions, alice, "ลบ 1", NOON + timedelta(seconds=5))
        await say(sessions, alice, "ยกเลิก", NOON + timedelta(seconds=10))

        assert await open_titles(sessions, alice) == ["อ่าน paper"]

    run_committed(scenario)


def test_another_message_clears_the_delete_question(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(sessions, alice, "ลบ 1", NOON + timedelta(seconds=5))
        await say(sessions, alice, "งานวันนี้", NOON + timedelta(seconds=10))
        await say(sessions, alice, "ยืนยันลบ #1", NOON + timedelta(seconds=15))

        assert await open_titles(sessions, alice) == ["อ่าน paper"]  # not deleted

    run_committed(scenario)


def test_an_old_delete_question_has_expired(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(sessions, alice, "ลบ 1", NOON + timedelta(seconds=5))
        await say(sessions, alice, "ยืนยันลบ #1", NOON + timedelta(minutes=11))

        assert await open_titles(sessions, alice) == ["อ่าน paper"]
        assert "หมดเวลาแล้ว" in texts(await replies_to(sessions, alice))[-1]

    run_committed(scenario)


def test_nobody_can_delete_someone_elses_task(make_user_id):
    alice, bob = make_user_id(), make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน งานของอลิซ")
        await say(sessions, bob, "ลบ 1")  # bob has no task #1

        assert texts(await replies_to(sessions, bob)) == ["ไม่พบงาน #1 ครับ"]
        await say(sessions, bob, "ยืนยันลบ #1", NOON + timedelta(seconds=5))
        assert await open_titles(sessions, alice) == ["งานของอลิซ"]

    run_committed(scenario)


def test_the_model_can_move_a_task_and_the_time_is_kept(make_user_id):
    alice = make_user_id()
    model = scripted(
        {"commands": [{"action": "update", "task_no": 1, "shift_days": 2}]}
    )

    async def scenario(sessions):
        await say(sessions, alice, "พรุ่งนี้บ่ายสองส่งรายงาน")  # 16 Oct 14:00
        await say(
            sessions,
            alice,
            "เลื่อนไปอีกสองวัน",
            NOON + timedelta(seconds=5),
            make_handler(model),
        )

        row = (await replies_to(sessions, alice))[-1]
        assert row.messages[0]["text"] == "✏️ แก้แล้ว #1 ส่งรายงาน · อา. 18 ต.ค. 14:00"
        assert row.critical is True

    run_committed(scenario)


def test_update_and_add_in_one_message(make_user_id):
    alice = make_user_id()
    model = scripted(
        {
            "commands": [
                {"action": "update", "task_no": 1, "title": "อ่าน paper บทที่ 2"},
                add("ซื้อนม", "2026-10-16"),
            ]
        }
    )

    async def scenario(sessions):
        await say(sessions, alice, "เพิ่มงาน อ่าน paper")
        await say(
            sessions, alice, "สองอย่าง", NOON + timedelta(seconds=5), make_handler(model)
        )

        body = texts(await replies_to(sessions, alice))[-1]
        assert "✏️ #1 อ่าน paper บทที่ 2" in body
        assert "➕ #2 ซื้อนม" in body

    run_committed(scenario)


def test_a_past_day_is_not_saved_and_the_user_is_told(make_user_id):
    alice = make_user_id()
    model = scripted({"commands": [add("ประชุม", "2026-10-10")]})

    async def scenario(sessions):
        handler = make_handler(model)
        await say(sessions, alice, "10 ต.ค. ประชุม", handler=handler)

        [row] = await replies_to(sessions, alice)
        assert "ผ่านไปแล้ว" in row.messages[0]["text"]
        async with sessions() as session:
            saved = await session.scalars(
                select(Task.title).join(User).where(User.line_user_id == alice)
            )
        assert list(saved) == []

    run_committed(scenario)


def test_in_a_batch_the_past_one_is_named_and_the_other_is_saved(make_user_id):
    alice = make_user_id()
    model = scripted(
        {"commands": [add("ส่งรายงาน", "2026-10-16"), add("ประชุม", "2026-10-10")]}
    )

    async def scenario(sessions):
        handler = make_handler(model)
        await say(sessions, alice, "พรุ่งนี้ส่งรายงาน กับประชุมเมื่อวันที่ 10", handler=handler)

        [row] = await replies_to(sessions, alice)
        body = row.messages[0]["text"]
        assert "➕ #1 ส่งรายงาน" in body
        assert "ผ่านไปแล้ว 1 รายการ: “ประชุม”" in body

    run_committed(scenario)


class SlowModel:
    """While the model 'thinks', try to change the user's row from another session."""

    name = "slow"

    def __init__(self, sessions, line_user_id):
        self.sessions = sessions
        self.line_user_id = line_user_id
        self.got_lock = None

    async def extract(self, text, context):
        try:
            async with self.sessions.begin() as session:
                await session.execute(text_sql("SET LOCAL lock_timeout = '500ms'"))
                await session.execute(
                    update(User)
                    .where(User.line_user_id == self.line_user_id)
                    .values(timezone=User.timezone)
                )
            self.got_lock = True
        except Exception:  # lock_timeout: someone still holds the row
            self.got_lock = False
        return json.dumps({"commands": [add("ซื้อนม", "2026-10-16")]})


def test_no_row_lock_is_held_while_the_model_thinks(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        model = SlowModel(sessions, alice)
        await say(sessions, alice, "พรุ่งนี้ซื้อนมหน่อย", handler=make_handler(model))

        assert model.got_lock is True
        [row] = await replies_to(sessions, alice)
        assert "ซื้อนม" in row.messages[0]["text"]  # the task was still saved

    run_committed(scenario)
