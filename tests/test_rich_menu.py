"""The rich menu script sends the right requests. No network: httpx2.MockTransport."""

import asyncio
import json

import httpx2

from scripts.setup_rich_menu import NAME, setup


def fake_line(existing: list[dict]):
    calls: list[tuple[str, str]] = []
    bodies: dict[str, bytes] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append((request.method, str(request.url)))
        bodies[f"{request.method} {request.url.path}"] = request.content
        if request.url.path == "/v2/bot/richmenu/list":
            return httpx2.Response(200, json={"richmenus": existing})
        if request.method == "POST" and request.url.path == "/v2/bot/richmenu":
            return httpx2.Response(200, json={"richMenuId": "richmenu-new"})
        return httpx2.Response(200, json={})

    return httpx2.MockTransport(handler), calls, bodies


def test_replaces_our_old_menu_and_makes_the_new_one_default():
    existing = [
        {"richMenuId": "richmenu-old", "name": NAME},
        {"richMenuId": "richmenu-other", "name": "someone else's"},
    ]
    transport, calls, bodies = fake_line(existing)

    menu_id = asyncio.run(setup("token", "2001234567-AbCd", b"PNG", transport))

    assert menu_id == "richmenu-new"
    assert calls == [
        ("GET", "https://api.line.me/v2/bot/richmenu/list"),
        ("DELETE", "https://api.line.me/v2/bot/richmenu/richmenu-old"),
        ("POST", "https://api.line.me/v2/bot/richmenu"),
        ("POST", "https://api-data.line.me/v2/bot/richmenu/richmenu-new/content"),
        ("POST", "https://api.line.me/v2/bot/user/all/richmenu/richmenu-new"),
    ]
    menu = json.loads(bodies["POST /v2/bot/richmenu"])
    actions = [area["action"] for area in menu["areas"]]
    assert actions == [
        {"type": "message", "text": "งานวันนี้"},
        {"type": "uri", "uri": "https://liff.line.me/2001234567-AbCd"},
        {"type": "message", "text": "วิธีใช้"},
    ]
    assert sum(area["bounds"]["width"] for area in menu["areas"]) == 2500
    assert bodies["POST /v2/bot/richmenu/richmenu-new/content"] == b"PNG"
