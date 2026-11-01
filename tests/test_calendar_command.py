"""Typing "ปฏิทิน" in the chat gives a button that opens the LIFF calendar."""

from app.ai.mock import MockProvider
from app.bot.handler import make_handler
from tests.helpers import run_committed
from tests.test_bot_handler import replies_to, say, texts


def test_calendar_card_opens_the_liff_url(make_user_id):
    alice = make_user_id()
    with_liff = make_handler(MockProvider(), liff_id="2001234567-AbCdEfGh")

    async def scenario(sessions):
        await say(sessions, alice, "ปฏิทิน", handler=with_liff)
        [row] = await replies_to(sessions, alice)
        card = row.messages[0]
        assert card["type"] == "flex"
        [button] = card["contents"]["footer"]["contents"]
        assert button["action"] == {
            "type": "uri",
            "label": "เปิดปฏิทิน",
            "uri": "https://liff.line.me/2001234567-AbCdEfGh",
        }

    run_committed(scenario)


def test_without_liff_the_bot_says_it_is_not_ready(make_user_id):
    alice = make_user_id()

    async def scenario(sessions):
        await say(sessions, alice, "ปฏิทิน")  # default handler: no LIFF_ID
        assert texts(await replies_to(sessions, alice)) == ["ปฏิทินยังไม่เปิดให้ใช้ครับ"]

    run_committed(scenario)
