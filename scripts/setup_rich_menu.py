"""Put the AI Planner menu (งานวันนี้ | ปฏิทิน | วิธีใช้) under the chat, for everyone.

Run once (and again after changing the picture or LIFF_ID):
    python -m scripts.setup_rich_menu

Needs LINE_CHANNEL_ACCESS_TOKEN and LIFF_ID in .env. An older menu with the same
name is deleted first, so running it twice never leaves two menus behind.
"""

import asyncio
import sys
from pathlib import Path

import httpx2

from app.config import get_settings

API = "https://api.line.me"
DATA_API = "https://api-data.line.me"  # uploads go to a different host
NAME = "AI Planner"
IMAGE = Path(__file__).resolve().parent.parent / "assets" / "richmenu.png"
WIDTH, HEIGHT = 2500, 843  # LINE's "compact" size; the picture must match


def menu(liff_id: str) -> dict:
    """Three equal tap areas over the picture, left to right."""

    def area(x: int, width: int, action: dict) -> dict:
        bounds = {"x": x, "y": 0, "width": width, "height": HEIGHT}
        return {"bounds": bounds, "action": action}

    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,  # open when the chat opens
        "name": NAME,
        "chatBarText": "เมนู",
        "areas": [
            area(0, 833, {"type": "message", "text": "งานวันนี้"}),
            area(833, 834, {"type": "uri", "uri": f"https://liff.line.me/{liff_id}"}),
            area(1667, 833, {"type": "message", "text": "วิธีใช้"}),
        ],
    }


async def setup(
    token: str,
    liff_id: str,
    image: bytes,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> str:
    """Create the menu, upload its picture, make it everyone's default. Returns its id."""
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(
        headers=headers, timeout=30, transport=transport
    ) as http:
        old = (await http.get(f"{API}/v2/bot/richmenu/list")).raise_for_status().json()
        for item in old["richmenus"]:
            if item["name"] == NAME:
                await http.delete(f"{API}/v2/bot/richmenu/{item['richMenuId']}")

        created = await http.post(f"{API}/v2/bot/richmenu", json=menu(liff_id))
        menu_id = created.raise_for_status().json()["richMenuId"]

        upload = await http.post(
            f"{DATA_API}/v2/bot/richmenu/{menu_id}/content",
            content=image,
            headers={"Content-Type": "image/png"},
        )
        upload.raise_for_status()

        default = await http.post(f"{API}/v2/bot/user/all/richmenu/{menu_id}")
        default.raise_for_status()
    return menu_id


def main() -> None:
    settings = get_settings()
    if settings.line_channel_access_token is None or not settings.liff_id:
        sys.exit("Set LINE_CHANNEL_ACCESS_TOKEN and LIFF_ID in .env first")
    token = settings.line_channel_access_token.get_secret_value()
    menu_id = asyncio.run(setup(token, settings.liff_id, IMAGE.read_bytes()))
    print(f"rich menu ready: {menu_id}")


if __name__ == "__main__":
    main()
